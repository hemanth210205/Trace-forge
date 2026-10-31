"""Execute a Traceforge experiment with the sample support agent.

Create the experiment in the console (Evaluations → a completed run → Create experiment) with configurations
the agent supports, e.g. baseline {"retriever": "keyword"} and candidate {"retriever": "stemmed"}, then:

    set TRACEFORGE_API_KEY=tf_...
    python examples/sample_agent/experiment.py --experiment <experiment-id>

Every dataset case runs once per arm with that arm's configuration; Traceforge then compares the arms. Nothing
here changes a deployed agent: the configuration only applies to this replay.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import traceforge  # noqa: E402
from agent import answer  # noqa: E402


async def task(case_input: dict, config: dict) -> dict:
    return {"answer": await answer(case_input["question"], config)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--experiment", required=True)
    parser.add_argument("--endpoint", default=None)
    parser.add_argument("--api-key", default=None)
    args = parser.parse_args()

    client = traceforge.init(api_key=args.api_key, endpoint=args.endpoint)
    if not client.enabled:
        sys.exit("No API key: pass --api-key or set TRACEFORGE_API_KEY.")
    try:
        experiment = traceforge.run_experiment(args.experiment, task)
    except traceforge.EvaluationError as exc:
        sys.exit(f"Experiment failed: {exc}")
    finally:
        client.shutdown()
    comparison = experiment.get("comparison", {})
    print(json.dumps({"experiment_id": experiment["id"], "status": experiment["status"],
                      "suggested_outcome": comparison.get("suggested_outcome"), "cases": comparison.get("cases")}, indent=2))
    sys.exit(0 if experiment["status"] == "completed" else 1)
