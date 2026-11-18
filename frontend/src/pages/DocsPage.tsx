import { useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import {
  ArrowRight,
  BookOpen,
  ChevronDown,
  FlaskConical,
  FolderGit2,
  GitCompare,
  Info,
  KeyRound,
  Lightbulb,
  ListTree,
  Database,
  Stethoscope,
} from "lucide-react";

import { AuthorCredit } from "../components/AuthorCredit";
import { Backdrop } from "../components/Backdrop";
import { CodeBlock } from "../components/CodeBlock";
import { PublicNav } from "../components/PublicNav";
import { cx } from "../components/ui";

// --- Table of contents ---------------------------------------------------------------------------------------

const TOC: { group: string; items: { id: string; label: string }[] }[] = [
  { group: "Getting started", items: [{ id: "overview", label: "Overview" }, { id: "quick-start", label: "Quick start" }] },
  {
    group: "Integrate the SDK",
    items: [
      { id: "install", label: "Install the SDK" },
      { id: "instrument", label: "Instrument your agent" },
      { id: "delivery", label: "Delivery & privacy" },
    ],
  },
  {
    group: "Modules",
    items: [
      { id: "projects", label: "Projects & API keys" },
      { id: "traces", label: "Traces" },
      { id: "datasets", label: "Datasets" },
      { id: "evaluations", label: "Evaluations" },
      { id: "diagnoses", label: "Diagnoses" },
      { id: "experiments", label: "Experiments" },
    ],
  },
  {
    group: "Reference",
    items: [
      { id: "sdk-reference", label: "SDK reference" },
      { id: "configuration", label: "Configuration" },
      { id: "rest-api", label: "REST API" },
      { id: "oauth", label: "Google & GitHub sign-in" },
      { id: "troubleshooting", label: "Troubleshooting" },
    ],
  },
];
const IDS = TOC.flatMap((g) => g.items.map((i) => i.id));

/** Highlights the section currently in view. */
function useActiveSection(): string {
  const [active, setActive] = useState(IDS[0]);
  useEffect(() => {
    if (typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries.filter((e) => e.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
        if (visible[0]) setActive(visible[0].target.id);
      },
      { rootMargin: "-80px 0px -65% 0px" },
    );
    IDS.forEach((id) => {
      const el = document.getElementById(id);
      if (el) observer.observe(el);
    });
    return () => observer.disconnect();
  }, []);
  return active;
}

function TocLinks({ active, onPick }: { active: string; onPick?: () => void }) {
  return (
    <nav aria-label="Documentation" className="space-y-6">
      {TOC.map((group) => (
        <div key={group.group}>
          <p className="px-3 font-mono text-2xs uppercase tracking-[0.14em] text-slate-500">{group.group}</p>
          <ul className="mt-2 space-y-0.5">
            {group.items.map((item) => (
              <li key={item.id}>
                <a
                  href={`#${item.id}`}
                  onClick={onPick}
                  className={cx(
                    "block rounded-lg px-3 py-1.5 text-[13px] transition-colors",
                    active === item.id
                      ? "bg-accent-400/10 font-medium text-accent-300 ring-1 ring-inset ring-accent-400/25"
                      : "text-slate-400 hover:bg-white/[0.04] hover:text-slate-100",
                  )}
                >
                  {item.label}
                </a>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </nav>
  );
}

// --- Building blocks -----------------------------------------------------------------------------------------

function Section({ id, eyebrow, title, children }: { id: string; eyebrow: string; title: string; children: ReactNode }) {
  return (
    <section id={id} className="scroll-mt-24 border-t border-white/[0.06] pt-12 first:border-t-0 first:pt-0">
      <p className="font-mono text-2xs tracking-[0.14em] text-pulse-400">{eyebrow}</p>
      <h2 className="mt-2 text-2xl font-semibold tracking-tight text-slate-50 sm:text-3xl">{title}</h2>
      <div className="mt-5 space-y-5 text-[15px] leading-relaxed text-slate-300">{children}</div>
    </section>
  );
}

function H3({ children }: { children: ReactNode }) {
  return <h3 className="pt-3 text-lg font-semibold text-slate-50">{children}</h3>;
}

function C({ children }: { children: ReactNode }) {
  return <code className="rounded-md border border-white/[0.08] bg-white/[0.04] px-1.5 py-0.5 font-mono text-[0.85em] text-accent-200">{children}</code>;
}

function Callout({ tone = "tip", title, children }: { tone?: "tip" | "note"; title: string; children: ReactNode }) {
  const Icon = tone === "tip" ? Lightbulb : Info;
  return (
    <div
      className={cx(
        "flex gap-3 rounded-2xl border px-4 py-3.5 text-sm",
        tone === "tip" ? "border-accent-400/25 bg-accent-400/[0.06]" : "border-pulse-400/25 bg-pulse-400/[0.06]",
      )}
    >
      <Icon className={cx("mt-0.5 h-4 w-4 shrink-0", tone === "tip" ? "text-accent-400" : "text-pulse-400")} aria-hidden />
      <div>
        <p className="font-semibold text-slate-100">{title}</p>
        <div className="mt-0.5 text-slate-300">{children}</div>
      </div>
    </div>
  );
}

function Table({ head, rows }: { head: string[]; rows: ReactNode[][] }) {
  return (
    <div className="overflow-x-auto rounded-2xl border border-white/[0.08]">
      <table className="w-full min-w-[520px] text-left text-sm">
        <thead className="bg-white/[0.03] text-xs text-slate-400">
          <tr>
            {head.map((h) => (
              <th key={h} className="px-4 py-2.5 font-medium">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-white/[0.06]">
          {rows.map((row, i) => (
            <tr key={i} className="align-top">
              {row.map((cell, j) => (
                <td key={j} className={cx("px-4 py-2.5", j === 0 ? "whitespace-nowrap text-slate-100" : "text-slate-400")}>{cell}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Steps({ items }: { items: { title: string; body: ReactNode }[] }) {
  return (
    <ol className="space-y-4">
      {items.map((item, i) => (
        <li key={item.title} className="flex gap-4">
          <span className="grid h-8 w-8 shrink-0 place-items-center rounded-xl bg-accent-400/10 font-mono text-xs font-semibold text-accent-400">
            {i + 1}
          </span>
          <div className="min-w-0 pt-1">
            <p className="font-medium text-slate-100">{item.title}</p>
            <div className="mt-0.5 text-sm text-slate-400">{item.body}</div>
          </div>
        </li>
      ))}
    </ol>
  );
}

function ModuleHeader({ icon: Icon, what }: { icon: typeof ListTree; what: ReactNode }) {
  return (
    <div className="tf-glass flex gap-4 rounded-2xl p-4">
      <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-accent-400/10 text-accent-400">
        <Icon className="h-5 w-5" aria-hidden />
      </span>
      <div className="text-sm">
        <p className="font-mono text-2xs tracking-[0.14em] text-slate-500">WHAT IT&apos;S FOR</p>
        <p className="mt-0.5 text-slate-200">{what}</p>
      </div>
    </div>
  );
}

// --- Code samples (all checked against sdk/python and the backend schemas) -------------------------------------

const INSTALL = `# from the root of the Traceforge repository
pip install -e sdk/python
python -c "import traceforge; print(traceforge.__version__)"`;

const FIRST_TRACE = `import traceforge

traceforge.init(api_key="tf_...", endpoint="http://127.0.0.1:8000")

with traceforge.trace("hello-agent", input={"question": "Hi"}) as t:
    with traceforge.span("answer", kind="llm") as llm:
        llm.set_output("Hello!")
    t.set_output("Hello!")`;

const ENV = `# instead of passing them to init()
export TRACEFORGE_API_KEY="tf_..."
export TRACEFORGE_ENDPOINT="http://127.0.0.1:8000"`;

const FULL_AGENT = `import traceforge

traceforge.init()  # reads TRACEFORGE_API_KEY and TRACEFORGE_ENDPOINT

@traceforge.observe(kind="tool")
def lookup_order(order_id: str) -> dict:
    return orders.get(order_id)          # arguments and return value are recorded

def answer(question: str) -> str:
    with traceforge.trace("support-request", input={"question": question},
                          tags=["prod"], user_id="u-42") as t:
        with traceforge.span("kb-search", kind="retrieval", input=question) as s:
            docs = search(question)
            s.add_documents([{"id": d.id, "source": d.path, "score": d.score} for d in docs])

        order = lookup_order("A1")

        with traceforge.span("compose-answer", kind="llm", input=question) as llm:
            reply = call_model(question, docs, order)
            llm.set_llm(model="your-model", prompt_tokens=812, completion_tokens=64, cost_usd=0.00021)
            llm.set_output(reply)

        t.set_output(reply)
        return reply`;

const ASYNC = `async def handle(question: str) -> str:
    async with traceforge.trace("support-request", input={"question": question}):
        return await run_agent(question)   # spans inside follow the asyncio task

# Threads do not inherit the current span automatically:
import contextvars
ctx = contextvars.copy_context()
pool.submit(ctx.run, work, item)`;

const CASES = `{"key": "shipping-a100", "input": {"question": "Where is my order A100?"},
 "expected_output": "Your order is shipped with ParcelCo.", "expected_sources": ["kb/shipping.md"],
 "metadata": {"topic": "orders"}}
{"key": "unknown-order", "input": {"question": "What is the status of order Z999?"},
 "expected_output": "I could not find that order. Please check the order number."}`;

const EVALUATE = `import traceforge

traceforge.init()

run = traceforge.create_evaluation_run(
    "Support QA", name="nightly",                      # dataset name; latest published version
    evaluators=[{"type": "normalized_match", "field": "answer"},
                {"type": "source_match"}],
)

def task(case_input: dict) -> dict:                   # your agent; never sees expected outputs
    return {"answer": my_agent(case_input["question"])}

finished = traceforge.evaluate(run["id"], task)
print(finished["status"], finished["pass_rate"])`;

const EXPERIMENT = `import traceforge

traceforge.init()

def task(case_input: dict, config: dict) -> dict:
    # config is the baseline or the candidate configuration from the experiment
    return {"answer": my_agent(case_input["question"], retriever=config["retriever"])}

experiment = traceforge.run_experiment("<experiment id>", task)
print(experiment["comparison"]["suggested_outcome"])`;

const OAUTH_ENV = `# only the providers you set are offered
TRACEFORGE_GITHUB_CLIENT_ID=...
TRACEFORGE_GITHUB_CLIENT_SECRET=...
TRACEFORGE_GOOGLE_CLIENT_ID=....apps.googleusercontent.com
TRACEFORGE_GOOGLE_CLIENT_SECRET=...
# the origin your browser uses for the console (default shown)
TRACEFORGE_PUBLIC_URL=http://127.0.0.1:8000`;

const CONFIGS = `// baseline                         // candidate
{"retriever": "keyword"}            {"retriever": "stemmed"}`;

// --- Page ------------------------------------------------------------------------------------------------------

export function DocsPage() {
  const active = useActiveSection();
  const [tocOpen, setTocOpen] = useState(false);

  return (
    // overflow-x-clip, not hidden: "hidden" makes this a scroll container and breaks the sticky sidebar.
    <div className="relative min-h-screen overflow-x-clip bg-app text-slate-100">
      <Backdrop />
      <div className="relative z-10">
        <PublicNav />

        {/* Header */}
        <header className="mx-auto max-w-6xl px-4 pb-10 pt-12 sm:px-6 lg:pt-16">
          <p className="inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/[0.03] px-3 py-1 font-mono text-2xs text-slate-300">
            <BookOpen className="h-3.5 w-3.5 text-accent-400" aria-hidden />
            DOCUMENTATION · v0.1
          </p>
          <h1 className="mt-5 text-4xl font-bold tracking-tight text-slate-50 sm:text-5xl">
            Traceforge <span className="tf-signal-text">docs</span>
          </h1>
          <p className="mt-5 max-w-2xl text-[15px] leading-relaxed text-slate-400">
            Install the SDK, connect your agent, and use each module, from the first trace to a validated fix.
          </p>
        </header>

        <div className="mx-auto grid max-w-6xl gap-10 px-4 pb-24 sm:px-6 lg:grid-cols-[220px_minmax(0,1fr)]">
          {/* Sidebar: sticky on desktop, a collapsible menu on small screens */}
          <aside className="lg:sticky lg:top-20 lg:max-h-[calc(100vh-6rem)] lg:self-start lg:overflow-y-auto">
            <button
              type="button"
              onClick={() => setTocOpen((o) => !o)}
              aria-expanded={tocOpen}
              className="tf-glass flex w-full items-center justify-between rounded-xl px-4 py-2.5 text-sm text-slate-200 lg:hidden"
            >
              On this page
              <ChevronDown className={cx("h-4 w-4 transition-transform", tocOpen && "rotate-180")} aria-hidden />
            </button>
            <div className={cx("mt-3 lg:mt-0 lg:block", tocOpen ? "block" : "hidden")}>
              <TocLinks active={active} onPick={() => setTocOpen(false)} />
            </div>
          </aside>

          <main className="min-w-0 space-y-12">
            {/* ---------------------------------------------------------------- Getting started */}
            <Section id="overview" eyebrow="GETTING STARTED" title="Overview">
              <p>
                Traceforge is an observability and evaluation platform for AI agents that runs on your own machine. You add a
                few lines of the Python SDK to your agent; each run is recorded as a <strong className="text-slate-100">trace</strong>{" "}
                made of <strong className="text-slate-100">spans</strong> (one per step: an LLM call, a tool call, a retrieval).
              </p>
              <div className="grid gap-3 sm:grid-cols-[1fr_auto_1fr_auto_1fr] sm:items-center" aria-label="How the parts connect">
                {[
                  ["Your agent", "+ Python SDK"],
                  ["Traceforge API", "FastAPI · PostgreSQL"],
                  ["Web console", "http://127.0.0.1:8000"],
                ].map(([title, sub], i) => (
                  <div key={title} className="contents">
                    <div className="tf-glass rounded-2xl px-4 py-3 text-center">
                      <p className="font-medium text-slate-50">{title}</p>
                      <p className="font-mono text-2xs text-slate-500">{sub}</p>
                    </div>
                    {i < 2 && <ArrowRight className="mx-auto h-4 w-4 rotate-90 text-accent-400 sm:rotate-0" aria-hidden />}
                  </div>
                ))}
              </div>
              <p>The console is organised around one loop, plus the projects and keys that hold it together:</p>
              <div className="grid gap-3 sm:grid-cols-2">
                {[
                  { icon: ListTree, id: "traces", title: "Traces", body: "See every step of every run." },
                  { icon: FlaskConical, id: "evaluations", title: "Evaluations", body: "Score the agent on a dataset." },
                  { icon: Stethoscope, id: "diagnoses", title: "Diagnoses", body: "Find out why cases failed." },
                  { icon: GitCompare, id: "experiments", title: "Experiments", body: "Prove a fix before shipping it." },
                ].map(({ icon: Icon, id, title, body }) => (
                  <a key={id} href={`#${id}`} className="tf-glass group flex items-center gap-3 rounded-2xl p-4 transition-transform hover:-translate-y-0.5">
                    <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-accent-400/10 text-accent-400">
                      <Icon className="h-5 w-5" aria-hidden />
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block font-medium text-slate-50">{title}</span>
                      <span className="block text-sm text-slate-400">{body}</span>
                    </span>
                    <ArrowRight className="h-4 w-4 text-slate-600 transition-colors group-hover:text-accent-400" aria-hidden />
                  </a>
                ))}
              </div>
            </Section>

            <Section id="quick-start" eyebrow="GETTING STARTED" title="Quick start">
              <p>
                You need a running Traceforge server (<C>python -m app.serve</C> from <C>backend/</C>, console at{" "}
                <C>http://127.0.0.1:8000</C>) and Python 3.10 or newer for the SDK.
              </p>
              <Steps
                items={[
                  { title: "Create an account", body: <>Use <Link className="text-accent-400 hover:underline" to="/register">Create account</Link>. Accounts are local to your server.</> },
                  { title: "Create a project", body: <>Projects → <strong className="text-slate-200">New project</strong>. Give it a name, the agent&apos;s name and an environment.</> },
                  { title: "Create an API key", body: <>The project opens on <strong className="text-slate-200">API keys</strong>. Name a key and copy it: it is shown only once.</> },
                  { title: "Install the SDK", body: <>Run <C>pip install -e sdk/python</C> from the repository root.</> },
                  { title: "Send your first trace", body: "Run the script below with your key." },
                  { title: "Open the Traces tab", body: "Your run appears with its span timeline." },
                ]}
              />
              <CodeBlock code={FIRST_TRACE} title="first_trace.py" />
              <CodeBlock code={ENV} lang="bash" title="environment" />
            </Section>

            {/* ---------------------------------------------------------------- Integrate */}
            <Section id="install" eyebrow="INTEGRATE THE SDK" title="Install the SDK">
              <p>
                The SDK is pure standard-library Python (3.10+), with no dependencies to install. It is installed from the
                Traceforge repository; it is not published on PyPI.
              </p>
              <CodeBlock code={INSTALL} lang="bash" />
              <p>
                Configure it once at start-up with <C>traceforge.init(api_key=..., endpoint=...)</C>, or set{" "}
                <C>TRACEFORGE_API_KEY</C> and <C>TRACEFORGE_ENDPOINT</C> and call <C>traceforge.init()</C>. The endpoint
                defaults to <C>http://127.0.0.1:8000</C>.
              </p>
              <Callout tone="note" title="No key, no crash">
                Without an API key the SDK logs one warning and becomes a no-op, so instrumented code still runs.
              </Callout>
            </Section>

            <Section id="instrument" eyebrow="INTEGRATE THE SDK" title="Instrument your agent">
              <p>
                The SDK records exactly what you instrument; it does not patch LLM or agent frameworks. Wrap one run in{" "}
                <C>trace()</C>, and each step in <C>span()</C> or a function decorated with <C>@observe</C>. Spans nest under
                the current span automatically.
              </p>
              <CodeBlock code={FULL_AGENT} title="agent.py" />

              <H3>Span kinds</H3>
              <Table
                head={["Kind", "Use it for"]}
                rows={[
                  [<C key="k">llm</C>, "A model call. Add usage with set_llm(model=, prompt_tokens=, completion_tokens=, cost_usd=)."],
                  [<C key="k">tool</C>, "A tool or API call. Use @observe(kind=\"tool\") or span.set_tool(name, arguments)."],
                  [<C key="k">retrieval</C>, "A search over documents. Record results with add_documents([{id, source, score, content}])."],
                  [<C key="k">agent</C>, "An agent or sub-agent that runs other steps."],
                  [<C key="k">chain</C>, "A fixed sequence of steps."],
                  [<C key="k">custom</C>, "Anything else (the default)."],
                ]}
              />
              <Callout title="Record sources on retrieval spans">
                The <C>source_match</C> evaluator and retrieval diagnoses read the <C>source</C> of documents added with{" "}
                <C>add_documents</C>. Use the same values as your dataset&apos;s <C>expected_sources</C>.
              </Callout>

              <H3>Tags, users and sessions</H3>
              <p>
                <C>trace(name, input=, metadata=, tags=, user_id=, session_id=)</C>. Tags become filters in the Trace Explorer;
                user and session ids show on the trace summary.
              </p>

              <H3>Errors</H3>
              <p>
                An exception that leaves a span or trace marks it <C>error</C> with its type, message and traceback, and is
                re-raised unchanged. To record a handled error, call <C>span.record_exception(exc)</C> or{" "}
                <C>span.set_status(&quot;error&quot;)</C>.
              </p>

              <H3>Async code and threads</H3>
              <CodeBlock code={ASYNC} title="async_agent.py" />
              <p>
                When context managers do not fit, use the manual API: <C>trace.start_span(...)</C>, <C>span.end()</C>,{" "}
                <C>trace.end()</C>.
              </p>
            </Section>

            <Section id="delivery" eyebrow="INTEGRATE THE SDK" title="Delivery & privacy">
              <ul className="list-disc space-y-2 pl-5 marker:text-accent-400">
                <li>Events go into a bounded in-memory queue and a background thread sends them in batches, so your agent never waits on the network.</li>
                <li>Network errors and HTTP 408, 429 and 5xx are retried with backoff. Telemetry never raises into your code; if the queue is full, events are dropped instead of blocking.</li>
                <li><C>traceforge.get_client().stats()</C> reports sent, dropped and failed counts. Remaining data is sent at interpreter exit, or call <C>traceforge.shutdown()</C>.</li>
                <li>Before data leaves your process, keys such as <C>password</C>, <C>api_key</C>, <C>token</C> and <C>secret</C> become <C>[REDACTED]</C>, and strings that look like keys or bearer tokens are masked.</li>
                <li>Add rules with <C>redact_keys=[...]</C> and <C>redact_patterns=[...]</C>, or stop payload capture with <C>capture_inputs=False</C> / <C>capture_outputs=False</C>.</li>
              </ul>
              <Callout tone="note" title="Redaction is pattern-based">
                It cannot recognise every secret or personal detail. Review what your agent records.
              </Callout>
            </Section>

            {/* ---------------------------------------------------------------- Modules */}
            <Section id="projects" eyebrow="MODULES" title="Projects & API keys">
              <ModuleHeader icon={FolderGit2} what="A project holds the traces, datasets, runs and keys of one agent. Users only ever see their own projects." />
              <Steps
                items={[
                  { title: "Create a project", body: "Projects → New project: name, agent name and environment (development, staging or production)." },
                  { title: "Create a key", body: <>API keys → name it → <strong className="text-slate-200">Create key</strong>. Copy it now; only a hash is stored.</> },
                  { title: "Manage keys", body: "The list shows when each key was last used. Keys do not expire; revoke the ones you no longer need." },
                ]}
              />
              <Callout tone="note" title="One key per project">
                A key can only send data to its own project. Use a separate project per agent or environment.
              </Callout>
            </Section>

            <Section id="traces" eyebrow="MODULES" title="Traces">
              <ModuleHeader icon={ListTree} what="The Trace Explorer: every run your agent sent, with the exact steps it took." />
              <H3>Find a run</H3>
              <p>
                The list is newest first. Search by name or exact id, and filter by status, time range, tag or{" "}
                <strong className="text-slate-100">spans with errors</strong>. Filters are kept in the URL, so you can share a
                filtered view.
              </p>
              <H3>Inspect it</H3>
              <ul className="list-disc space-y-2 pl-5 marker:text-accent-400">
                <li><strong className="text-slate-100">Summary:</strong> status, duration, errors, tokens, cost, user and session.</li>
                <li><strong className="text-slate-100">Timeline:</strong> the span tree with a waterfall of when each step ran.</li>
                <li><strong className="text-slate-100">Span details:</strong> errors and stack traces, model and tokens, tool arguments, retrieved documents, inputs and outputs.</li>
              </ul>
              <Callout title="Safe to display">Trace content is always shown as plain text, never run as code or HTML.</Callout>
            </Section>

            <Section id="datasets" eyebrow="MODULES" title="Datasets">
              <ModuleHeader icon={Database} what="Test cases for your agent: an input, the expected output, and the sources it should retrieve." />
              <Steps
                items={[
                  { title: "Create a dataset", body: "Datasets → New Dataset." },
                  { title: "Add cases", body: "Add and edit cases in the draft, or import a JSON or JSONL file (append or replace). Every error is listed by line." },
                  { title: "Publish a version", body: "Publishing freezes the draft as an immutable version. Evaluations always run on an exact version." },
                ]}
              />
              <p>One case per line in JSONL. <C>key</C> is optional (generated if omitted); <C>expected_sources</C> is used by <C>source_match</C>.</p>
              <CodeBlock code={CASES} lang="json" title="support_qa.jsonl" numbers={false} />
            </Section>

            <Section id="evaluations" eyebrow="MODULES" title="Evaluations">
              <ModuleHeader
                icon={FlaskConical}
                what="Score your agent on a published dataset version with deterministic evaluators. Traceforge does not run your agent: your code runs each case and submits the output."
              />
              <H3>Run one from your code</H3>
              <CodeBlock code={EVALUATE} title="evaluate.py" />
              <p>
                Each case runs in its own trace, tagged <C>evaluation</C>. The task can be sync or async; if it raises, that
                case is recorded as an execution error and the run continues. You can also create the run in the console
                (dataset → Evaluations) and pass its id.
              </p>
              <H3>Evaluators</H3>
              <p>Every evaluator accepts <C>field</C> (a dotted path into the output, e.g. <C>answer</C>) and an optional <C>name</C>.</p>
              <Table
                head={["Type", "Passes when", "Options"]}
                rows={[
                  [<C key="t">exact_match</C>, "The output equals the expected output exactly.", "none"],
                  [<C key="t">normalized_match</C>, "They match after normalising case, spacing and punctuation.", <C key="o">ignore_punctuation</C>],
                  [<C key="t">contains</C>, "The output contains the expected text or keywords.", <><C>keywords</C>, <C>mode</C> all | any, <C>case_sensitive</C></>],
                  [<C key="t">json_match</C>, "Output and expected output are equal JSON values.", <C key="o">allow_extra_keys</C>],
                  [<C key="t">required_fields</C>, "The output contains all required fields.", <C key="o">fields</C>],
                  [<C key="t">source_match</C>, "Retrieval spans found the expected sources.", <C key="o">min_recall</C>],
                ]}
              />
              <H3>Run states</H3>
              <p>
                <C>pending → running → scoring → completed | incomplete | failed</C>. A run with missing cases is{" "}
                <C>incomplete</C>; a pass rate is only reported for <C>completed</C> runs. Results show expected vs actual output,
                an explanation and a link to each case&apos;s trace.
              </p>
            </Section>

            <Section id="diagnoses" eyebrow="MODULES" title="Diagnoses">
              <ModuleHeader icon={Stethoscope} what="An investigation of why evaluation cases failed, built from the evidence in their traces." />
              <Steps
                items={[
                  { title: "Start it", body: <>Open a scored evaluation run and click <strong className="text-slate-200">Diagnose failures</strong>. It runs in the background.</> },
                  { title: "Read the facts", body: "Deterministic facts from evaluator results, retrieved sources and their ranks, and word overlap with documents and tool results." },
                  { title: "Review the hypotheses", body: "Each proposes a cause, cites supporting and contradicting facts, gives a confidence and recommends an experiment." },
                ]}
              />
              <Table
                head={["Failure category", "What it points to"]}
                rows={[
                  ["Retrieval", "The right documents were not retrieved."],
                  ["Context selection", "They were retrieved but not the ones used."],
                  ["Grounding", "The answer is not supported by the evidence."],
                  ["Tool execution / selection", "A tool failed, or the wrong tool was used."],
                  ["Reasoning", "The steps were right but the conclusion was not."],
                  ["Output format", "The answer is missing fields or is not valid JSON."],
                  ["Insufficient evidence", "No usable trace, so no cause is guessed."],
                ]}
              />
              <p>
                The server sets each hypothesis&apos;s evidence status (<C>supported</C>, <C>unsupported</C>, <C>contradicted</C>{" "}
                or <C>inconclusive</C>) with fixed rules and caps its confidence.
              </p>
              <Callout title="A hypothesis is not a conclusion">
                Hypotheses stay unvalidated until an experiment reproduces the failure in the baseline and fixes it in the
                candidate.
              </Callout>
              <p className="text-sm text-slate-400">
                Diagnosis uses the offline, rule-based <C>mock</C> provider by default. A Claude provider (
                <C>TRACEFORGE_DIAGNOSIS_PROVIDER=anthropic</C>) is experimental.
              </p>
            </Section>

            <Section id="experiments" eyebrow="MODULES" title="Experiments">
              <ModuleHeader icon={GitCompare} what="Compare a baseline and a candidate configuration of your agent on the same dataset version and evaluators." />
              <Steps
                items={[
                  { title: "Create it", body: "From a completed evaluation run, enter the two configurations as JSON." },
                  { title: "Run it from your code", body: "run_experiment calls your task once per case for each configuration." },
                  { title: "Compare", body: "Pass rates, per-evaluator results, retrieval recall, fixed and regressed cases, latency, tokens and cost." },
                  { title: "Validate and decide", body: "Optionally validate a diagnosis hypothesis, then record an approve or reject decision with a rationale." },
                ]}
              />
              <CodeBlock code={CONFIGS} lang="json" title="configurations" numbers={false} />
              <CodeBlock code={EXPERIMENT} title="experiment.py" />
              <Callout tone="note" title="Decisions never deploy anything">
                Approving records your judgement (append-only and audited). Apply the candidate configuration yourself. An
                interrupted run can be started again: arms already scored are skipped.
              </Callout>
            </Section>

            {/* ---------------------------------------------------------------- Reference */}
            <Section id="sdk-reference" eyebrow="REFERENCE" title="SDK reference">
              <Table
                head={["API", "Use"]}
                rows={[
                  [<C key="a">init(api_key=, endpoint=, **options)</C>, "Create the global client. See Configuration."],
                  [<C key="a">trace(name, input=, metadata=, tags=, user_id=, session_id=)</C>, "One end-to-end run (with / async with)."],
                  [<C key="a">span(name, kind, input=, metadata=, attributes=)</C>, "One step; nests under the current span."],
                  [<C key="a">@observe(name=None, kind=...)</C>, "Wrap a sync or async function as a span."],
                  [<C key="a">span.set_llm(model=, prompt_tokens=, completion_tokens=, total_tokens=, cost_usd=)</C>, "LLM usage."],
                  [<C key="a">span.set_tool(name, arguments)</C>, "Tool calls."],
                  [<C key="a">span.add_documents([...])</C>, "Retrieved documents: id, source, score, content."],
                  [<C key="a">set_input · set_output · set_metadata · set_status · record_exception</C>, "On traces and spans."],
                  [<C key="a">create_evaluation_run(dataset, name=, evaluators=, version=)</C>, "Create a pending run (latest version by default)."],
                  [<C key="a">evaluate(run_id, task)</C>, "Execute a run with task(case_input)."],
                  [<C key="a">run_experiment(experiment_id, task)</C>, "Execute an experiment with task(case_input, config)."],
                  [<C key="a">get_client().stats() · shutdown()</C>, "Delivery counts; flush and stop."],
                ]}
              />
            </Section>

            <Section id="configuration" eyebrow="REFERENCE" title="Configuration">
              <p>Options for <C>traceforge.init()</C>:</p>
              <Table
                head={["Option", "Default", "Effect"]}
                rows={[
                  [<C key="o">capture_inputs</C>, "True", "Record span and trace inputs."],
                  [<C key="o">capture_outputs</C>, "True", "Record outputs."],
                  [<C key="o">redact_keys</C>, "()", "Extra keys to replace with [REDACTED]."],
                  [<C key="o">redact_patterns</C>, "()", "Extra regexes to mask."],
                  [<C key="o">max_field_bytes</C>, "32 KB", "Larger fields become a truncated preview."],
                  [<C key="o">max_queue_size</C>, "10,000", "Buffered events before dropping."],
                  [<C key="o">max_batch_items</C>, "100", "Events per request."],
                  [<C key="o">flush_interval</C>, "1.0 s", "How often batches are sent."],
                  [<C key="o">max_retries</C>, "3", "Retries for network errors, 408, 429 and 5xx."],
                  [<C key="o">raise_on_error</C>, "False", "Raise delivery errors (for debugging)."],
                  [<C key="o">enabled</C>, "True", "Turn the SDK off entirely."],
                ]}
              />
            </Section>

            <Section id="rest-api" eyebrow="REFERENCE" title="REST API">
              <p>
                Everything the console and SDK do is available under <C>/api/v1</C>. Interactive docs are at{" "}
                <C>http://127.0.0.1:8000/api/docs</C>.
              </p>
              <Table
                head={["Auth", "Used by"]}
                rows={[
                  ["Session cookie + X-CSRF-Token", "The console. State-changing requests need the CSRF header."],
                  [<C key="a">Authorization: Bearer tf_...</C>, "The SDK and scripts, with a project API key."],
                ]}
              />
              <p>
                The SDK uses <C>POST /ingest/batch</C>, <C>/sdk/eval-runs</C> and <C>/sdk/experiments</C>. Every error uses one
                JSON envelope with a <C>request_id</C>.
              </p>
            </Section>

            <Section id="oauth" eyebrow="REFERENCE" title="Google & GitHub sign-in">
              <p>
                &ldquo;Continue with Google&rdquo; and &ldquo;Continue with GitHub&rdquo; are off until you register an OAuth
                app with the provider and give the server its credentials. The secret stays on the server; the browser never
                sees it.
              </p>
              <Steps
                items={[
                  {
                    title: "GitHub",
                    body: (
                      <>
                        Settings → Developer settings → OAuth Apps → <strong className="text-slate-200">New OAuth App</strong>.
                        Homepage URL <C>http://127.0.0.1:8000</C>, callback URL{" "}
                        <C>http://127.0.0.1:8000/api/v1/auth/oauth/github/callback</C>. Then generate a client secret.
                      </>
                    ),
                  },
                  {
                    title: "Google",
                    body: (
                      <>
                        Google Cloud console → APIs &amp; Services → Credentials → Create credentials →{" "}
                        <strong className="text-slate-200">OAuth client ID</strong> (Web application). Authorised redirect URI{" "}
                        <C>http://127.0.0.1:8000/api/v1/auth/oauth/google/callback</C>. Set up the consent screen first if asked.
                      </>
                    ),
                  },
                  { title: "Add the credentials", body: "Put them in the .env file at the repository root, then restart the server." },
                ]}
              />
              <CodeBlock code={OAUTH_ENV} lang="bash" title=".env" numbers={false} />
              <Callout tone="note" title="Using the Vite dev server?">
                Set <C>TRACEFORGE_PUBLIC_URL=http://localhost:5173</C> and register the callback URLs with that origin
                instead, so the session cookie lands on the page you are using.
              </Callout>
              <ul className="list-disc space-y-2 pl-5 marker:text-accent-400">
                <li>Only provider-verified email addresses are accepted.</li>
                <li>A first sign-in creates an account without a password, or links an existing account with the same email.</li>
                <li>After that, the provider account is recognised by its id, even if its email changes.</li>
              </ul>
            </Section>

            <Section id="troubleshooting" eyebrow="REFERENCE" title="Troubleshooting">
              <Table
                head={["Symptom", "What to check"]}
                rows={[
                  ["No traces appear", "The key belongs to this project and the endpoint is right. Print traceforge.get_client().stats()."],
                  [<C key="s">dropped a batch (network: URLError)</C>, "The server is not running or the endpoint is wrong."],
                  [<C key="s">invalid_api_key</C>, "The key was revoked or mistyped. Create a new one."],
                  ["Run is incomplete", "Some cases got no output. Re-run the evaluation."],
                  ["source_match always fails", "Retrieval spans need add_documents with source values matching expected_sources."],
                ]}
              />
              <div className="tf-glass flex flex-col items-start justify-between gap-4 rounded-2xl p-5 sm:flex-row sm:items-center">
                <div>
                  <p className="font-medium text-slate-50">Ready to send your first trace?</p>
                  <p className="text-sm text-slate-400">Create an account, add a project and copy your API key.</p>
                </div>
                <Link
                  to="/register"
                  className="inline-flex items-center gap-2 rounded-full bg-accent-400 px-5 py-2.5 text-sm font-semibold text-[#09090B] transition hover:brightness-105"
                >
                  <KeyRound className="h-4 w-4" aria-hidden /> Get started
                </Link>
              </div>
            </Section>
          </main>
        </div>

        <footer className="border-t border-border bg-app/70 px-4 py-4 backdrop-blur sm:px-6">
          <AuthorCredit className="mx-auto max-w-6xl" />
        </footer>
      </div>
    </div>
  );
}
