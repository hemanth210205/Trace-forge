# Customer Support Deterministic AI Agent

A deterministic Python customer support agent integrated with the **Traceforge** Observability, Evaluation & Diagnosis Platform using the official Traceforge Python SDK (`traceforge`).

---

## 1. Overview & Architecture

The Customer Support Agent simulates an enterprise tier-1 customer assistance pipeline with pure determinism (no external LLM or vector DB network dependencies):
1. **Query Classification**: Categorizes user intent into `order_inquiry`, `refund_calculation`, `warranty_policy`, `shipping_policy`, or `returns_policy`.
2. **Knowledge Retrieval**: Retrieves relevant documentation from local Markdown policy files using keyword and stemmed matching algorithms, capturing document retrieval metadata with `span.add_documents()`.
3. **Context Selection**: Chooses primary context documents from retrieved candidates.
4. **Tool Execution**: Invokes deterministic tools:
   - `order-lookup-tool`: Looks up delivery statuses, carriers, and destinations.
   - `refund-calculator-tool`: Computes deductions, net refund amounts, and audit breakdowns.
5. **Answer Generation**: Generates policy-grounded answers, recording model attributes, token metrics, and execution costs via `span.set_llm()`.

---

## 2. Directory Structure

```text
agents/customer_support_agent/
│
├── config.py                 # Directory-agnostic environment and path configuration
├── agent.py                  # Core agent workflow with hierarchical Traceforge SDK tracing
├── sync_dataset.py           # Syncs the 12-case benchmark dataset into Traceforge
├── run_evaluation.py         # Executes SDK evaluation run on the live dataset
├── run_diagnosis.py          # Triggers automated root-cause diagnosis for failed cases
├── run_experiment.py         # Executes a controlled baseline vs. candidate experiment
├── requirements.txt          # Python dependencies
├── .env.example              # Configuration template
├── .env                      # Local environment configuration (gitignored)
│
├── knowledge_base/           # Domain policy documentation
│   ├── returns.md            # Return eligibility, 30-day window, refund rules
│   ├── shipping.md           # Shipping tiers, delivery estimates, delay policies
│   └── warranty.md           # 1-year limited hardware warranty and exclusions
│
├── tools/                    # SDK-instrumented executable tools
│   ├── order_lookup.py       # Order lookup tool with controlled failure triggers
│   └── refund_calculator.py  # Refund calculator with deduction audit breakdowns
│
├── scenarios/
│   └── test_cases.json       # 12 deterministic test cases covering 8 diagnosis categories
│
└── tests/
    ├── test_agent.py         # Workflow, intent classification, and scenario tests
    └── test_tools.py         # Unit tests for order lookup and refund calculation
```

---

## 3. Environment Setup

Copy `.env.example` to `.env` and configure your Traceforge parameters:

```bash
# Traceforge Backend API URL
TRACEFORGE_ENDPOINT=http://127.0.0.1:8000

# Traceforge API Key (scoped to target project)
TRACEFORGE_API_KEY=tf_...

# Traceforge Project ID (Customer Support Agent project UUID)
TRACEFORGE_PROJECT_ID=9f7a4e00-c182-4eb6-8afb-49c80d1dbe3e
```

---

## 4. Running Tests

Run the full agent test suite with pytest:

```powershell
.venv\Scripts\python.exe -m pytest agents/customer_support_agent/tests
```

All 24 test cases verify:
- Knowledge base loading and query intent classification.
- Document retrieval strategies (`keyword`, `stemmed`, `force_empty`, `force_irrelevant`).
- Context selection (`best_rank`, `query_overlap`, `force_wrong`).
- All 10 scenario modes (`normal`, `retrieval_failure_empty`, `retrieval_failure_irrelevant`, `context_selection_failure`, `reasoning_failure`, `tool_selection_failure`, `tool_execution_failure`, `grounding_failure`, `output_format_failure`, `insufficient_evidence`).
- Tool execution, arithmetic validations, and error handling.

---

## 5. Traceforge Platform Integration

### Step 1: Run a Traced Query
Execute a single query through the agent to verify trace and span ingestion into Traceforge:

```powershell
.venv\Scripts\python.exe -c "from agents.customer_support_agent.agent import run; print(run('What does the standard hardware warranty cover?'))"
```

View the generated trace in Traceforge Console at `http://localhost:5173/projects/9f7a4e00-c182-4eb6-8afb-49c80d1dbe3e/traces`.

### Step 2: Sync Benchmark Dataset
Sync the 12-case benchmark dataset into Traceforge:

```powershell
.venv\Scripts\python.exe -m agents.customer_support_agent.sync_dataset
```

### Step 3: Run Evaluation Run
Execute evaluation scoring across all 12 benchmark cases:

```powershell
.venv\Scripts\python.exe -m agents.customer_support_agent.run_evaluation
```

### Step 4: Run Root-Cause Diagnosis
Run the 11-step automated diagnosis engine across the failed evaluation cases:

```powershell
.venv\Scripts\python.exe -m agents.customer_support_agent.run_diagnosis
```

### Step 5: Run Controlled Experiment
Run an A/B baseline versus candidate configuration experiment:

```powershell
.venv\Scripts\python.exe -m agents.customer_support_agent.run_experiment
```
