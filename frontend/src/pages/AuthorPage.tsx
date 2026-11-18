import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { Activity, ArrowLeft, ArrowUpRight, FileSearch, Gauge, GraduationCap, Mail, Phone, Sparkles, Workflow } from "lucide-react";

import { useAuth } from "../auth/AuthContext";
import { AUTHOR, GitHubMark, LinkedInMark } from "../components/AuthorCredit";
import { Backdrop } from "../components/Backdrop";
import { BrandMark } from "../components/BrandMark";
import { cx } from "../components/ui";

const FOCUS = [
  {
    icon: FileSearch,
    title: "Retrieval-augmented generation",
    body: "RAG architectures that ground a model's answers in the right documents, and measure when retrieval misses.",
  },
  {
    icon: Workflow,
    title: "Multi-agent workflows",
    body: "Agents that plan, call tools and hand work to each other, with every step recorded and inspectable.",
  },
  {
    icon: Gauge,
    title: "Low-latency inference",
    body: "Inference pipelines tuned for response time and cost, so a working prototype stays fast under real traffic.",
  },
  {
    icon: Activity,
    title: "Observable LLM systems",
    body: "Tracing, evaluation and diagnosis for models in production. Traceforge is my take on this.",
  },
];

const STACK = ["Python", "FastAPI", "PostgreSQL", "SQLAlchemy", "React", "TypeScript", "Tailwind CSS", "Playwright"];

// AI capabilities Traceforge actually implements (see README: Features, Evaluators, Diagnosis).
const AI_TERMS = [
  "LLM tracing",
  "Agent observability",
  "Span telemetry",
  "Tool-call tracing",
  "RAG evaluation",
  "Retrieval recall",
  "Token & cost tracking",
  "Deterministic evaluators",
  "Versioned eval datasets",
  "Grounding checks",
  "Root-cause diagnosis",
  "Evidence-backed hypotheses",
  "A/B config experiments",
  "Regression detection",
  "PII & secret redaction",
  "Claude provider (experimental)",
];

function ContactRow({ href, icon, label, value, external }: { href: string; icon: ReactNode; label: string; value: string; external?: boolean }) {
  return (
    <a
      href={href}
      {...(external ? { target: "_blank", rel: "noopener noreferrer" } : {})}
      className="group flex items-center gap-3 rounded-2xl border border-white/[0.07] bg-white/[0.025] px-3.5 py-3 transition-all hover:border-accent-400/40 hover:bg-accent-400/[0.06]"
    >
      <span className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-white/[0.05] text-slate-300 transition-colors group-hover:bg-accent-400/15 group-hover:text-accent-300">
        {icon}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block text-2xs text-slate-500">{label}</span>
        <span className="block truncate text-sm font-medium text-slate-100">{value}</span>
      </span>
      <ArrowUpRight className="h-4 w-4 shrink-0 text-slate-600 transition-colors group-hover:text-accent-400" aria-hidden />
    </a>
  );
}

function PillLink({ href, children, primary, external }: { href: string; children: ReactNode; primary?: boolean; external?: boolean }) {
  return (
    <a
      href={href}
      {...(external ? { target: "_blank", rel: "noopener noreferrer" } : {})}
      className={cx(
        "inline-flex h-11 items-center gap-2 rounded-full px-5 text-sm font-semibold transition-all",
        primary
          ? "bg-gradient-to-r from-accent-400 to-pulse-400 text-[#09090B] shadow-[0_10px_36px_-10px_rgba(184,255,0,0.55)] hover:brightness-110"
          : "border border-white/10 bg-white/[0.03] text-slate-100 backdrop-blur hover:border-white/20 hover:bg-white/[0.06]",
      )}
    >
      {children}
    </a>
  );
}

export function AuthorPage() {
  const { user } = useAuth();

  return (
    <div className="relative min-h-screen overflow-x-hidden bg-app text-slate-100">
      <Backdrop intensity="vivid" />

      <div className="relative z-10">
        {/* Top bar */}
        <header className="mx-auto flex h-16 max-w-6xl items-center justify-between px-4 sm:px-6">
          <Link to="/" className="flex items-center gap-2.5">
            <BrandMark className="h-7 w-7" />
            <span className="text-[15px] font-semibold tracking-tight text-slate-50">Traceforge</span>
          </Link>
          <Link
            to={user ? "/" : "/login"}
            className="inline-flex items-center gap-1.5 rounded-full border border-white/10 bg-white/[0.03] px-3.5 py-1.5 text-xs font-medium text-slate-200 backdrop-blur transition-colors hover:border-slate-600 hover:text-slate-50"
          >
            <ArrowLeft className="h-3.5 w-3.5" aria-hidden />
            {user ? "Back to console" : "Sign in"}
          </Link>
        </header>

        {/* Hero */}
        <section className="mx-auto grid max-w-6xl gap-12 px-4 pb-20 pt-10 sm:px-6 lg:grid-cols-[1.25fr_1fr] lg:items-center lg:pt-16">
          <div className="tf-rise">
            <p className="text-xs font-semibold tracking-[0.18em] text-pulse-400">BUILT BY</p>
            <h1 className="tf-signal-gradient mt-3 pb-1 text-5xl font-bold leading-[1.05] tracking-tight sm:text-7xl">{AUTHOR.name}</h1>
            <p className="mt-6 text-xl font-semibold leading-snug text-slate-50 sm:text-2xl">{AUTHOR.role}</p>
            <p className="mt-1 text-[15px] font-medium text-slate-300">
              {AUTHOR.degree} graduate · IIIT Surat
            </p>
            <p className="mt-5 max-w-xl text-[15px] leading-relaxed text-slate-300">
              I'm an AI Engineer focused on moving large language models from prototype notebooks into resilient,
              observable production systems. My work centres on retrieval-augmented generation (RAG) architectures,
              multi-agent workflows and low-latency inference pipelines.
            </p>
            <p className="mt-3 max-w-xl text-[15px] leading-relaxed text-slate-400">
              Traceforge comes from that work: a platform to trace, evaluate and debug AI agents, so a failure is found
              from evidence in its traces instead of guesswork.
            </p>
            <div className="mt-8 flex flex-wrap gap-3">
              <PillLink href={`mailto:${AUTHOR.email}`} primary>
                <Mail className="h-4 w-4" aria-hidden /> Email me
              </PillLink>
              <PillLink href={AUTHOR.phoneHref}>
                <Phone className="h-4 w-4" aria-hidden /> Call
              </PillLink>
              <PillLink href={AUTHOR.linkedin} external>
                <LinkedInMark className="h-4 w-4" /> LinkedIn
              </PillLink>
              <PillLink href={AUTHOR.github} external>
                <GitHubMark className="h-4 w-4" /> GitHub
              </PillLink>
            </div>
          </div>

          {/* Profile card */}
          <aside
            aria-label="Contact details"
            className="tf-rise tf-glass rounded-3xl p-6 [animation-delay:120ms] sm:p-7"
          >
            <div className="flex items-center gap-4">
              <div className="grid h-16 w-16 shrink-0 place-items-center rounded-2xl bg-gradient-to-br from-accent-300 via-accent-400 to-pulse-400 text-xl font-bold text-[#09090B] shadow-[0_12px_40px_-12px_rgba(184,255,0,0.6)]">
                {AUTHOR.initials}
              </div>
              <div className="min-w-0">
                <h2 className="text-xl font-semibold text-slate-50">{AUTHOR.name}</h2>
                <p className="text-sm text-slate-400">{AUTHOR.role}</p>
              </div>
            </div>
            <p className="mt-4 text-sm leading-relaxed text-slate-400">
              Building RAG systems, multi-agent workflows and low-latency inference that hold up in production.
            </p>

            <div className="mt-5 space-y-2.5">
              <ContactRow href={AUTHOR.phoneHref} icon={<Phone className="h-4 w-4" />} label="Phone" value={AUTHOR.phone} />
              <ContactRow href={`mailto:${AUTHOR.email}`} icon={<Mail className="h-4 w-4" />} label="Email" value={AUTHOR.email} />
              <ContactRow href={AUTHOR.linkedin} icon={<LinkedInMark className="h-4 w-4" />} label="LinkedIn" value={AUTHOR.linkedinLabel} external />
              <ContactRow href={AUTHOR.github} icon={<GitHubMark className="h-4 w-4" />} label="GitHub" value={AUTHOR.githubLabel} external />
            </div>

            <div className="mt-5 flex items-start gap-3 border-t border-border pt-5">
              <GraduationCap className="mt-0.5 h-5 w-5 shrink-0 text-accent-400" aria-hidden />
              <div>
                <p className="text-sm font-medium text-slate-100">{AUTHOR.school}</p>
                <p className="text-xs text-slate-400">{AUTHOR.degree} · Graduate</p>
              </div>
            </div>
          </aside>
        </section>

        {/* Focus areas */}
        <section className="mx-auto max-w-6xl px-4 pb-20 sm:px-6" aria-labelledby="focus-heading">
          <div className="flex items-end justify-between gap-4 border-b border-border pb-4">
            <h2 id="focus-heading" className="text-2xl font-semibold tracking-tight text-slate-50">What I work on</h2>
            <span className="hidden text-xs text-slate-500 sm:block">From notebook to production</span>
          </div>
          <div className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {FOCUS.map(({ icon: Icon, title, body }, i) => (
              <div
                key={title}
                className="tf-rise tf-glass group relative overflow-hidden rounded-2xl p-5 transition-transform duration-300 hover:-translate-y-1"
                style={{ animationDelay: `${200 + i * 80}ms` }}
              >
                <span className="absolute right-4 top-4 font-mono text-2xs text-slate-600">0{i + 1}</span>
                <span className="grid h-10 w-10 place-items-center rounded-xl bg-accent-400/10 text-accent-400">
                  <Icon className="h-5 w-5" aria-hidden />
                </span>
                <h3 className="mt-4 text-[15px] font-semibold text-slate-50">{title}</h3>
                <p className="mt-2 text-sm leading-relaxed text-slate-400">{body}</p>
                <div className="absolute inset-x-0 bottom-0 h-px scale-x-0 bg-gradient-to-r from-transparent via-accent-400 to-transparent transition-transform duration-500 group-hover:scale-x-100" />
              </div>
            ))}
          </div>
        </section>

        {/* About the project */}
        <section className="mx-auto max-w-6xl px-4 pb-20 sm:px-6">
          <div className="tf-glass grid gap-8 rounded-3xl p-6 sm:p-8 lg:grid-cols-[1fr_1.1fr]">
            <div>
              <p className="text-xs font-semibold tracking-[0.18em] text-accent-400">THIS PROJECT</p>
              <h2 className="mt-2 text-2xl font-semibold tracking-tight text-slate-50">Traceforge</h2>
              <p className="mt-3 text-sm leading-relaxed text-slate-300">
                A Python SDK records every step an agent takes as a trace. A versioned dataset scores the agent's answers.
                For failed cases, Traceforge collects evidence from the traces and proposes likely causes. A controlled
                experiment then checks whether a configuration change actually fixes them.
              </p>
              <ol className="mt-5 flex flex-wrap items-center gap-2 text-xs font-medium text-slate-300">
                {["Trace", "Evaluate", "Diagnose", "Experiment"].map((step, i) => (
                  <li key={step} className="flex items-center gap-2">
                    <span className="rounded-full border border-white/10 bg-white/[0.03] px-3 py-1">
                      <span className="mr-1.5 font-mono text-accent-400">{i + 1}</span>
                      {step}
                    </span>
                    {i < 3 && <span className="text-slate-600" aria-hidden>→</span>}
                  </li>
                ))}
              </ol>
            </div>
            <div className="space-y-6">
              <div>
                <p className="flex items-center gap-2 text-xs font-medium text-slate-400">
                  <Sparkles className="h-3.5 w-3.5 text-accent-400" aria-hidden />
                  AI &amp; LLMOps
                </p>
                <div className="mt-3 flex flex-wrap gap-2">
                  {AI_TERMS.map((term) => (
                    <span
                      key={term}
                      className="rounded-full border border-accent-400/25 bg-accent-400/[0.07] px-3 py-1 text-xs font-medium text-accent-200"
                    >
                      {term}
                    </span>
                  ))}
                </div>
              </div>
              <div>
                <p className="text-xs font-medium text-slate-400">Built with</p>
                <div className="mt-3 flex flex-wrap gap-2">
                  {STACK.map((tech) => (
                    <span key={tech} className="rounded-full border border-white/10 bg-white/[0.03] px-3 py-1 font-mono text-xs text-slate-200">
                      {tech}
                    </span>
                  ))}
                </div>
              </div>
            </div>
          </div>
        </section>

        {/* Call to action band */}
        <section className="mx-auto max-w-6xl px-4 pb-16 sm:px-6">
          <div className="relative overflow-hidden rounded-3xl bg-gradient-to-br from-accent-300 via-accent-400 to-pulse-400 text-[#09090B]">
          <div
            aria-hidden
            className="absolute inset-0 opacity-[0.10] [background-image:linear-gradient(to_right,#000_1px,transparent_1px),linear-gradient(to_bottom,#000_1px,transparent_1px)] [background-size:44px_44px]"
          />
          <div className="relative flex flex-col items-start gap-8 px-6 py-14 sm:px-10 lg:flex-row lg:items-center lg:justify-between lg:py-16">
            <h2 className="max-w-2xl text-3xl font-bold leading-tight tracking-tight sm:text-5xl">
              Building LLM systems that need to hold up in production?
            </h2>
            <a
              href={`mailto:${AUTHOR.email}`}
              className="inline-flex h-14 shrink-0 items-center gap-2.5 rounded-full bg-[#09090B] px-8 text-base font-semibold text-accent-400 shadow-[0_12px_30px_-12px_rgba(0,0,0,0.7)] transition-transform hover:scale-[1.03]"
            >
              Let's talk <ArrowUpRight className="h-5 w-5" aria-hidden />
            </a>
          </div>
          </div>
        </section>

        <footer className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-2 px-4 py-6 text-xs text-slate-500 sm:px-6">
          <span>© {new Date().getFullYear()} {AUTHOR.name}</span>
          <span>Traceforge v0.1.0</span>
        </footer>
      </div>
    </div>
  );
}
