import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { Link, Navigate, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import {
  AlertCircle,
  ArrowDown,
  ArrowRight,
  Atom,
  Braces,
  Container,
  Database,
  FileCode,
  FlaskConical,
  FolderPlus,
  GitCompare,
  HardDrive,
  ListTree,
  Server,
  ShieldCheck,
  Stethoscope,
  LockKeyhole,
  Terminal,
  UserPlus,
} from "lucide-react";

import { api, ApiError } from "../api/client";
import { useApi } from "../api/useApi";
import { useAuth } from "../auth/AuthContext";
import { AuthorCredit } from "../components/AuthorCredit";
import { Backdrop } from "../components/Backdrop";
import { CodeBlock } from "../components/CodeBlock";
import { LiveTrace } from "../components/LiveTrace";
import { PublicNav } from "../components/PublicNav";
import { OAuthButtons, oauthErrorMessage } from "../components/OAuthButtons";
import { Button, CopyButton, Field, PasswordInput, TextInput } from "../components/ui";

type Mode = "login" | "register";
// oauth: anyone may sign up with Google/GitHub; a password sign-up needs an invitation code.
type RegistrationMode = "open" | "disabled" | "domain" | "invite" | "oauth";

/** The front page: what Traceforge is, how to use it, and the sign-in / create-account form. */
export function AuthPage({ mode }: { mode: Mode }) {
  const { user } = useAuth();
  const location = useLocation();
  const next = (location.state as { from?: string } | null)?.from ?? "/";
  if (user) return <Navigate to={next} replace />;

  return (
    <div className="relative min-h-screen overflow-x-hidden bg-app text-slate-100">
      <Backdrop intensity="vivid" />
      <div className="relative z-10">
        <PublicNav onLanding mode={mode} />

        {/* Hero */}
        <section className="mx-auto grid max-w-6xl gap-10 px-4 pb-16 pt-10 sm:px-6 lg:grid-cols-12 lg:gap-x-10 lg:pt-16">
          <div className="tf-rise lg:col-span-7">
            <p className="inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/[0.03] px-3 py-1 font-mono text-2xs text-slate-300 backdrop-blur">
              <span className="h-2 w-2 rounded-full bg-accent-400" aria-hidden />
              AGENT OBSERVABILITY · LOCAL-FIRST
            </p>
            <h1 className="mt-6 text-4xl font-bold leading-[1.05] tracking-tight text-slate-50 sm:text-5xl xl:text-6xl">
              See every step your <span className="tf-signal-text">agent</span> takes.
            </h1>
            <p className="mt-6 max-w-xl text-[15px] leading-relaxed text-slate-400">
              Traceforge records each run of your AI agent, scores it against test cases, explains why answers failed, and
              proves whether a fix works before you ship it.
            </p>
            <div className="mt-7 flex flex-wrap items-center gap-x-6 gap-y-3 font-mono text-2xs text-slate-400">
              <Trust icon={<HardDrive className="h-3.5 w-3.5" />}>Runs on your machine</Trust>
              <Trust icon={<Braces className="h-3.5 w-3.5" />}>Python SDK</Trust>
              <Trust icon={<ShieldCheck className="h-3.5 w-3.5" />}>MIT licensed</Trust>
            </div>
            <a
              href="#how"
              className="mt-8 inline-flex items-center gap-2 border-b border-transparent font-mono text-xs text-pulse-400 transition-colors hover:border-pulse-400"
            >
              How it works <ArrowDown className="h-3.5 w-3.5" aria-hidden />
            </a>
          </div>

          <div className="tf-rise lg:col-span-5 lg:col-start-8 lg:row-span-2 lg:row-start-1 lg:pt-4 [animation-delay:120ms]">
            <AuthCard mode={mode} />
          </div>

          <div className="tf-rise lg:col-span-7 [animation-delay:200ms]">
            <LiveTrace />
          </div>
        </section>

        <StackStrip />
        <LoopSection />
        <HowToSection />
        <CallToAction mode={mode} />

        <footer className="border-t border-border bg-app/70 px-4 py-4 backdrop-blur sm:px-6">
          <AuthorCredit className="mx-auto max-w-6xl" />
        </footer>
      </div>
    </div>
  );
}

function Trust({ icon, children }: { icon: ReactNode; children: ReactNode }) {
  return (
    <span className="inline-flex items-center gap-2">
      <span className="text-accent-400">{icon}</span>
      {children}
    </span>
  );
}

function AuthCard({ mode }: { mode: Mode }) {
  const { login, register } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  // Invitation links carry the code in the fragment (#invite=...), which browsers never send to the server.
  const [inviteCode, setInviteCode] = useState(() => new URLSearchParams(location.hash.slice(1)).get("invite") ?? "");
  const registration = useApi((signal) => api<{ mode: RegistrationMode }>("/auth/registration", { signal }), []);
  const registrationMode = registration.data?.mode ?? "open";
  const needsCode = registrationMode === "invite" || registrationMode === "oauth";
  const [searchParams] = useSearchParams();
  // Set by the backend when a Google/GitHub sign-in fails: /login?oauth_error=<code>.
  const [error, setError] = useState<string | null>(() => oauthErrorMessage(searchParams.get("oauth_error")));
  const [busy, setBusy] = useState(false);
  const next = (location.state as { from?: string } | null)?.from ?? "/";

  useEffect(() => {
    // Drop the code from the address bar (and history) once it has been read.
    if (location.hash.includes("invite=")) window.history.replaceState(null, "", location.pathname + location.search);
  }, [location.hash, location.pathname, location.search]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (mode === "login") await login(email, password);
      else await register(email, password, displayName, needsCode ? inviteCode.trim() : undefined);
      navigate(next, { replace: true });
    } catch (err) {
      if (err instanceof ApiError && err.code === "too_many_attempts") {
        const wait = (err.details as { retry_after_seconds?: number } | null)?.retry_after_seconds;
        setError(`Too many failed attempts. Try again in ${wait ?? "a few"} seconds.`);
      } else if (err instanceof ApiError && err.code === "validation_error") {
        setError(mode === "register" ? "Check the fields: passwords need at least 12 characters." : "Enter your email and password.");
      } else setError(err instanceof Error ? err.message : "Something went wrong.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      id="start"
      className="relative scroll-mt-24 rounded-3xl bg-gradient-to-br from-accent-400/70 via-white/[0.08] to-pulse-400/60 p-px shadow-[0_40px_100px_-30px_rgba(0,0,0,1),0_0_90px_-30px_rgba(184,255,0,0.35)]"
    >
      <div className="relative overflow-hidden rounded-[calc(1.75rem-1px)] bg-[#0C0C10] p-6 sm:p-8">
      {/* Soft light at the top of the panel */}
      <div aria-hidden className="pointer-events-none absolute inset-x-0 top-0 h-40 bg-[radial-gradient(420px_160px_at_50%_0%,rgba(184,255,0,0.09),transparent_70%)]" />
      <div className="relative flex items-start justify-between gap-4 border-b border-white/[0.07] pb-5">
        <div>
          <p className="font-mono text-2xs tracking-[0.14em] text-pulse-400">{mode === "login" ? "ACCESS // SIGN IN" : "ACCESS // NEW ACCOUNT"}</p>
          <h2 className="mt-1.5 text-2xl font-semibold tracking-tight text-slate-50">
            {mode === "login" ? "Welcome back" : "Create your account"}
          </h2>
          <p className="mt-1 text-sm text-slate-400">
            {mode === "login" ? "Sign in to open your projects." : "It takes less than a minute."}
          </p>
        </div>
        <span className="grid h-11 w-11 shrink-0 place-items-center rounded-2xl border border-accent-400/30 bg-accent-400/10 text-accent-400">
          {mode === "login" ? <LockKeyhole className="h-5 w-5" aria-hidden /> : <UserPlus className="h-5 w-5" aria-hidden />}
        </span>
      </div>

      {mode === "register" && registrationMode === "disabled" ? (
        <div role="status" className="relative mt-6 rounded-2xl border border-pulse-400/25 bg-pulse-400/[0.06] p-4 text-sm text-slate-300">
          <p className="font-medium text-slate-100">Registration is closed</p>
          <p className="mt-1">New accounts are created by the administrator of this server. If you already have one, sign in.</p>
        </div>
      ) : (
      <>
      {mode === "register" && registrationMode === "oauth" && (
        <div role="status" className="relative mt-6 rounded-2xl border border-accent-400/25 bg-accent-400/[0.06] p-4 text-sm text-slate-300">
          <p className="font-medium text-slate-100">Sign up with Google or GitHub</p>
          <p className="mt-1">
            Anyone can create an account with the buttons below. A password account needs an invitation code.
          </p>
        </div>
      )}
      <form onSubmit={submit} className="relative mt-6 space-y-4">
        {mode === "register" && (
          <Field label="Name">
            <TextInput large value={displayName} onChange={(e) => setDisplayName(e.target.value)} required maxLength={100} autoComplete="name" placeholder="Your name" />
          </Field>
        )}
        {mode === "register" && needsCode && (
          <Field label="Invitation code" hint="From your invitation link. It works only for the address it was sent to.">
            <TextInput large value={inviteCode} onChange={(e) => setInviteCode(e.target.value)} required maxLength={200} autoComplete="off" spellCheck={false} />
          </Field>
        )}
        <Field label="Email" hint={mode === "register" && registrationMode === "domain" ? "Use an address from a domain this server accepts." : undefined}>
          <TextInput large type="email" value={email} onChange={(e) => setEmail(e.target.value)} required autoComplete="email" placeholder="you@example.com" />
        </Field>
        <Field label="Password" hint={mode === "register" ? "At least 12 characters." : undefined}>
          <PasswordInput
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            minLength={mode === "register" ? 12 : 1}
            autoComplete={mode === "login" ? "current-password" : "new-password"}
          />
        </Field>
        {error && (
          <div role="alert" className="flex items-start gap-2 rounded-xl border border-red-500/30 bg-red-950/30 px-3 py-2 text-xs text-red-200">
            <AlertCircle className="mt-px h-4 w-4 shrink-0 text-red-400" />
            <span>{error}</span>
          </div>
        )}
        <Button variant="primary" size="lg" type="submit" disabled={busy} className="!mt-6 h-12 w-full rounded-xl bg-gradient-to-r from-accent-400 to-accent-300 text-[15px] shadow-[0_14px_36px_-12px_rgba(184,255,0,0.65)] hover:brightness-105">
          {busy ? "Please wait…" : mode === "login" ? "Sign in" : "Create account"}
          {!busy && <ArrowRight className="h-4 w-4" aria-hidden />}
        </Button>
      </form>

      <div className="relative">
        <OAuthButtons next={next} />
      </div>
      </>
      )}

      <p className="relative mt-6 text-center text-sm text-slate-400">
        {mode === "login" ? "No account yet? " : "Already have an account? "}
        <Link className="font-medium text-accent-400 hover:underline" to={mode === "login" ? "/register" : "/login"}>
          {mode === "login" ? "Create one" : "Sign in"}
        </Link>
      </p>
      </div>
    </div>
  );
}

const STACK = [
  { icon: Braces, label: "Python SDK" },
  { icon: Server, label: "FastAPI" },
  { icon: Database, label: "PostgreSQL 16" },
  { icon: Atom, label: "React" },
  { icon: FileCode, label: "TypeScript" },
  { icon: Container, label: "Docker" },
  { icon: ShieldCheck, label: "Playwright tested" },
  { icon: HardDrive, label: "Runs locally" },
];

function StackStrip() {
  const items = [...STACK, ...STACK];
  return (
    <section aria-label="Built with" className="border-y border-border bg-sidebar/70 backdrop-blur">
      <div className="mx-auto flex max-w-6xl items-center gap-6 px-4 sm:px-6">
        <span className="shrink-0 py-4 font-mono text-2xs tracking-[0.14em] text-slate-500">BUILT ON</span>
        <div className="relative flex-1 overflow-hidden [mask-image:linear-gradient(to_right,transparent,#000_8%,#000_92%,transparent)]">
          <ul className="tf-marquee flex w-max gap-10 py-4">
            {items.map(({ icon: Icon, label }, i) => (
              <li key={i} aria-hidden={i >= STACK.length} className="flex items-center gap-2 font-mono text-xs text-slate-300">
                <Icon className="h-4 w-4 text-slate-500" />
                {label}
              </li>
            ))}
          </ul>
        </div>
      </div>
    </section>
  );
}

const LOOP = [
  { icon: ListTree, title: "Trace", body: "Every LLM call, tool call and retrieval becomes a span you can inspect." },
  { icon: FlaskConical, title: "Evaluate", body: "Score answers against a versioned dataset of test cases." },
  { icon: Stethoscope, title: "Diagnose", body: "For failed cases, get likely causes backed by evidence from the traces." },
  { icon: GitCompare, title: "Experiment", body: "Compare a baseline and a candidate configuration, case by case." },
];

function SectionHeading({ eyebrow, title, children }: { eyebrow: string; title: string; children?: ReactNode }) {
  return (
    <div className="max-w-2xl">
      <p className="font-mono text-2xs tracking-[0.14em] text-accent-400">{eyebrow}</p>
      <h2 className="mt-2 text-3xl font-semibold tracking-tight text-slate-50 sm:text-4xl">{title}</h2>
      {children && <p className="mt-3 text-[15px] leading-relaxed text-slate-400">{children}</p>}
    </div>
  );
}

function LoopSection() {
  return (
    <section id="platform" className="mx-auto max-w-6xl scroll-mt-20 px-4 py-20 sm:px-6">
      <SectionHeading eyebrow="01 // THE PLATFORM" title="One loop, from trace to fix.">
        Four steps that turn &ldquo;the agent got it wrong&rdquo; into a change you can trust.
      </SectionHeading>
      <ol className="mt-10 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {LOOP.map(({ icon: Icon, title, body }, i) => (
          <li
            key={title}
            className="tf-glass group relative overflow-hidden rounded-2xl p-6 transition-transform duration-300 hover:-translate-y-1"
          >
            <div className="flex items-center justify-between">
              <span className="grid h-11 w-11 place-items-center rounded-xl bg-accent-400/10 text-accent-400 transition-colors group-hover:bg-accent-400 group-hover:text-[#09090B]">
                <Icon className="h-5 w-5" aria-hidden />
              </span>
              <span className="font-mono text-3xl font-semibold text-white/[0.07] transition-colors group-hover:text-white/[0.14]">0{i + 1}</span>
            </div>
            <h3 className="mt-6 text-lg font-semibold text-slate-50">{title}</h3>
            <p className="mt-2 text-sm leading-relaxed text-slate-400">{body}</p>
            <span className="absolute inset-x-0 top-0 h-0.5 origin-left scale-x-0 bg-accent-400 transition-transform duration-300 group-hover:scale-x-100" />
          </li>
        ))}
      </ol>
    </section>
  );
}

const STEPS = [
  { icon: UserPlus, title: "Create an account", body: "Sign up on this page. Your data stays on your machine." },
  { icon: FolderPlus, title: "Add a project and an API key", body: "One project per agent. The key is shown once." },
  { icon: Terminal, title: "Install the SDK and wrap your agent", body: "A few lines record each run as a trace." },
  { icon: FlaskConical, title: "Run an evaluation", body: "Publish a dataset version, then score your agent on it." },
  { icon: GitCompare, title: "Diagnose and experiment", body: "Find why cases failed, then test the fix side by side." },
];

const CODE = `import traceforge

traceforge.init(api_key="tf_...", endpoint="http://127.0.0.1:8000")

# search() and call_model() stand for your agent's own code
with traceforge.trace("support-request", input={"question": q}):
    with traceforge.span("kb-search", kind="retrieval") as s:
        s.add_documents(search(q))
    with traceforge.span("answer", kind="llm") as llm:
        llm.set_output(call_model(q))`;

function HowToSection() {
  return (
    <section id="how" className="border-t border-border bg-sidebar/40 backdrop-blur-sm">
      <div className="mx-auto max-w-6xl scroll-mt-20 px-4 py-20 sm:px-6">
        <SectionHeading eyebrow="02 // HOW TO USE IT" title="From sign-up to your first trace." />
        <div className="mt-10 grid gap-10 lg:grid-cols-[1fr_1.1fr]">
          <ol className="relative min-w-0 space-y-6">
            <span className="absolute bottom-5 left-5 top-5 w-px bg-gradient-to-b from-accent-400 via-pulse-500 to-transparent" aria-hidden />
            {STEPS.map(({ icon: Icon, title, body }, i) => (
              <li key={title} className="relative flex gap-4">
                <span className="relative z-10 grid h-10 w-10 shrink-0 place-items-center rounded-xl border border-white/10 bg-[#0F0F13] text-accent-400">
                  <Icon className="h-4 w-4" aria-hidden />
                </span>
                <div className="pt-1">
                  <p className="font-mono text-2xs text-slate-500">STEP 0{i + 1}</p>
                  <h3 className="text-[15px] font-semibold text-slate-50">{title}</h3>
                  <p className="mt-0.5 text-sm text-slate-400">{body}</p>
                </div>
              </li>
            ))}
          </ol>

          <div className="min-w-0 space-y-4">
            <div className="tf-glass flex items-center justify-between rounded-2xl px-4 py-3 font-mono text-xs">
              <span>
                <span className="text-slate-600">$ </span>
                <span className="text-accent-300">pip install -e sdk/python</span>
              </span>
              <CopyButton value="pip install -e sdk/python" />
            </div>
            <CodeBlock code={CODE} title="agent.py" />
            <p className="font-mono text-2xs leading-relaxed text-slate-500">
              Run the install from the repository root (the SDK installs from source). Then open the project&apos;s
              Traces tab: each run shows up with its span timeline.
            </p>
          </div>
        </div>
      </div>
    </section>
  );
}

function CallToAction({ mode }: { mode: Mode }) {
  return (
    <section className="mx-auto max-w-6xl px-4 py-16 sm:px-6">
      <div className="relative overflow-hidden rounded-3xl bg-gradient-to-br from-accent-300 via-accent-400 to-pulse-400 text-[#09090B]">
      <div
        aria-hidden
        className="absolute inset-0 opacity-[0.10] [background-image:linear-gradient(to_right,#000_1px,transparent_1px),linear-gradient(to_bottom,#000_1px,transparent_1px)] [background-size:48px_48px]"
      />
      <div className="relative flex flex-col items-start gap-8 px-6 py-14 sm:px-10 lg:flex-row lg:items-center lg:justify-between lg:py-16">
        <div>
          <p className="font-mono text-xs font-semibold tracking-[0.14em]">READY WHEN YOU ARE</p>
          <h2 className="mt-3 max-w-2xl text-3xl font-bold leading-tight tracking-tight sm:text-5xl">Start tracing your agent today.</h2>
        </div>
        <Link
          to={mode === "login" ? "/register" : "/login"}
          onClick={() => window.scrollTo({ top: 0 })}
          className="inline-flex h-14 shrink-0 items-center gap-3 rounded-full bg-[#09090B] px-8 font-mono text-sm font-semibold uppercase tracking-wider text-accent-400 shadow-[0_12px_30px_-12px_rgba(0,0,0,0.7)] transition-transform hover:-translate-y-0.5"
        >
          {mode === "login" ? "Create an account" : "Sign in"} <ArrowRight className="h-5 w-5" aria-hidden />
        </Link>
      </div>
      </div>
    </section>
  );
}
