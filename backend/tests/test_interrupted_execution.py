"""Real interruption: an experiment executor process is killed mid-run (``os._exit``, no cleanup, no flush).

The experiment must stay consistent: a resumed executor completes it with a full comparison; one that is never
resumed is failed by the recovery sweep once its deadline passes, and a late executor is then refused cleanly
instead of producing a partial result.
"""

import os
import subprocess
import sys
from datetime import timedelta

from fastapi import FastAPI
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.base import utcnow
from app.serve import SERVER_OPTIONS
from app.services.recovery import EXPIRED_EXPERIMENT, recover_stale_work
from tests.conftest import BACKEND_DIR
from tests.test_evaluation_runs import Env
from tests.test_experiments import create, env as experiment_env  # noqa: F401  (fixture)
from tests.test_server_runtime import serve_in_thread

SDK_SRC = BACKEND_DIR.parent / "sdk" / "python" / "src"
EXECUTOR = """
import os, sys, traceforge
calls = 0
def task(case_input, config):
    global calls
    calls += 1
    if os.environ.get("CRASH_AT") and calls == int(os.environ["CRASH_AT"]):
        os._exit(9)  # the process dies: no exception handling, no telemetry flush, no completion call
    return {"answer": "Your order is shipped." if config["retriever"] == "stemmed" else "?"}
traceforge.init(api_key=os.environ["TRACEFORGE_API_KEY"], endpoint=sys.argv[2])
try:
    print(traceforge.run_experiment(sys.argv[1], task)["status"])
except traceforge.EvaluationError as exc:
    print("refused:", exc)
    sys.exit(3)
"""


def execute(base_url: str, env: Env, experiment_id: str, crash_at: int | None = None) -> subprocess.CompletedProcess[str]:
    child_env = {**os.environ, "PYTHONPATH": str(SDK_SRC), "TRACEFORGE_API_KEY": env.key}
    child_env.pop("CRASH_AT", None)
    if crash_at:
        child_env["CRASH_AT"] = str(crash_at)
    return subprocess.run([sys.executable, "-c", EXECUTOR, experiment_id, base_url], env=child_env,
                          capture_output=True, text=True, timeout=120)


def test_killed_executor_can_resume_or_is_failed_by_recovery(experiment_env: Env, app: FastAPI, db: Session, settings: Settings) -> None:  # noqa: F811
    env = experiment_env
    resumed = create(env).json()
    abandoned = create(env, name="abandoned").json()
    with serve_in_thread(app, **SERVER_OPTIONS) as base_url:
        # Dies on the first candidate case: the baseline arm was submitted, the candidate arm was not.
        crashed = execute(base_url, env, resumed["id"], crash_at=4)
        assert crashed.returncode == 9, crashed.stderr
        mid = env.alice.get(f"{env.base}/experiments/{resumed['id']}").json()
        assert mid["status"] == "running" and mid["comparison"] == {}
        assert mid["baseline"]["run_status"] == "completed" and mid["candidate"]["run_status"] in ("pending", "running")

        again = execute(base_url, env, resumed["id"])
        assert again.returncode == 0 and again.stdout.strip() == "completed", again.stdout + again.stderr
        done = env.alice.get(f"{env.base}/experiments/{resumed['id']}").json()
        assert done["comparison"]["arms"]["baseline"]["total_cases"] == done["comparison"]["arms"]["candidate"]["total_cases"] == 3
        assert done["comparison"]["cases"]["incomplete"] == []

        # The second executor dies and nobody resumes it.
        assert execute(base_url, env, abandoned["id"], crash_at=2).returncode == 9
        assert recover_stale_work(db, settings).experiments == 0  # deadline not reached: still resumable
        assert recover_stale_work(db, settings, now=utcnow() + timedelta(seconds=settings.experiment_time_limit_seconds + 1)).experiments == 1
        failed = env.alice.get(f"{env.base}/experiments/{abandoned['id']}").json()
        assert failed["status"] == "failed" and failed["failure_reason"] == EXPIRED_EXPERIMENT and failed["comparison"] == {}
        assert failed["baseline"]["run_status"] == failed["candidate"]["run_status"] == "failed"

        late = execute(base_url, env, abandoned["id"])
        assert late.returncode == 3 and late.stdout.startswith("refused:"), late.stdout + late.stderr
    assert env.alice.get(f"{env.base}/experiments/{abandoned['id']}").json() == failed  # nothing changed afterwards
