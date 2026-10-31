"""Execute a Traceforge evaluation run with the sample support agent.

Create the run in the console (Datasets → dataset → Evaluations) and pass its id, or let this script create
one over the latest published version of a dataset:

    set TRACEFORGE_API_KEY=tf_...
    python examples/sample_agent/evaluate.py --run <run-id>
    python examples/sample_agent/evaluate.py --dataset "Support QA"

Each case runs in its own trace; the server scores the outputs once the run is finished.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import traceforge  # noqa: E402
from agent import answer  # noqa: E402

DEFAULT_EVALUATORS = [
    {"type": "normalized_match", "field": "answer"},
    {"type": "source_match"},
    {"type": "required_fields", "fields": ["answer"]},
]


async def task(case_input: dict) -> dict:
    return {"answer": await answer(case_input["question"])}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--run", help="Id of an existing pending evaluation run")
    target.add_argument("--dataset", help="Create a run over the latest version of this dataset")
    parser.add_argument("--endpoint", default=None)
    parser.add_argument("--api-key", default=None)
    args = parser.parse_args()

    client = traceforge.init(api_key=args.api_key, endpoint=args.endpoint)
    if not client.enabled:
        sys.exit("No API key: pass --api-key or set TRACEFORGE_API_KEY.")
    run_id = args.run or traceforge.create_evaluation_run(args.dataset, name="sample-agent", evaluators=DEFAULT_EVALUATORS)["id"]
    try:
        run = traceforge.evaluate(run_id, task)
    except traceforge.EvaluationError as exc:
        sys.exit(f"Evaluation failed: {exc}")
    finally:
        client.shutdown()
    print(json.dumps({"run_id": run["id"], "status": run["status"], "pass_rate": run["pass_rate"],
                      "summary": run["summary"]}, indent=2))
    sys.exit(0 if run["status"] in ("completed", "incomplete") else 1)
