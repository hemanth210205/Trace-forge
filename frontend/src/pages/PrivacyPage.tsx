import type { ReactNode } from "react";
import { ShieldCheck } from "lucide-react";

import { AUTHOR, AuthorCredit } from "../components/AuthorCredit";
import { Backdrop } from "../components/Backdrop";
import { PublicNav } from "../components/PublicNav";

// Keep this page true to what the code does: see backend/app/models (stored fields), api/routes/auth.py
// (cookies) and services/oauth.py (provider scopes). Update LAST_UPDATED whenever the content changes.
const LAST_UPDATED = "18 November 2026";

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="tf-glass rounded-2xl p-6 sm:p-7">
      <h2 className="text-lg font-semibold text-slate-50">{title}</h2>
      <div className="mt-3 space-y-3 text-sm leading-relaxed text-slate-300">{children}</div>
    </section>
  );
}

function List({ items }: { items: ReactNode[] }) {
  return (
    <ul className="list-disc space-y-2 pl-5 marker:text-accent-400">
      {items.map((item, i) => (
        <li key={i}>{item}</li>
      ))}
    </ul>
  );
}

const Code = ({ children }: { children: ReactNode }) => (
  <code className="rounded bg-white/[0.06] px-1.5 py-0.5 font-mono text-xs text-slate-200">{children}</code>
);

export function PrivacyPage() {
  const mail = (
    <a href={`mailto:${AUTHOR.email}`} className="font-medium text-accent-300 underline-offset-2 hover:underline">
      {AUTHOR.email}
    </a>
  );

  return (
    <div className="relative min-h-screen overflow-x-clip bg-app text-slate-100">
      <Backdrop />
      <div className="relative z-10">
        <PublicNav />

        <header className="mx-auto max-w-3xl px-4 pb-8 pt-12 sm:px-6 lg:pt-16">
          <p className="inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/[0.03] px-3 py-1 font-mono text-2xs text-slate-300">
            <ShieldCheck className="h-3.5 w-3.5 text-accent-400" aria-hidden />
            PRIVACY POLICY
          </p>
          <h1 className="mt-5 text-4xl font-bold tracking-tight text-slate-50 sm:text-5xl">
            Your data on <span className="tf-signal-text">Traceforge</span>
          </h1>
          <p className="mt-5 text-[15px] leading-relaxed text-slate-400">
            What this site stores, why, and how to have it removed. Traceforge is run by {AUTHOR.name}; questions go
            to {mail}.
          </p>
          <p className="mt-2 font-mono text-2xs text-slate-500">Last updated: {LAST_UPDATED}</p>
        </header>

        <main className="mx-auto max-w-3xl space-y-5 px-4 pb-24 sm:px-6">
          <Section title="What is stored">
            <List
              items={[
                <>
                  <strong className="text-slate-100">Your account:</strong> email address and display name. If you sign
                  up with a password, only an Argon2 hash of it is stored, never the password itself.
                </>,
                <>
                  <strong className="text-slate-100">Google or GitHub sign-in:</strong> which provider you used, the
                  provider's ID for your account, and the verified email address it reports. Traceforge asks only for
                  basic profile access (Google: <Code>openid email profile</Code>; GitHub:{" "}
                  <Code>read:user user:email</Code>). The provider's access token is used once to read that profile and
                  is not stored. Traceforge gets no access to your repositories, files or mail.
                </>,
                <>
                  <strong className="text-slate-100">What you add:</strong> projects, datasets, evaluation runs,
                  diagnoses and experiments, and the traces your agents send with your API keys. API keys and session
                  tokens are stored only as hashes.
                </>,
                <>
                  <strong className="text-slate-100">A security log</strong> of account and project actions (for
                  example an API key created or a project deleted), linked to your account.
                </>,
              ]}
            />
          </Section>

          <Section title="Cookies">
            <p>Only cookies needed to sign you in and keep requests safe. No analytics, advertising or tracking cookies.</p>
            <List
              items={[
                <>
                  <Code>tf_session</Code>: keeps you signed in (7 days unless you sign out). Not readable by scripts.
                </>,
                <>
                  <Code>tf_csrf</Code>: proves that changes come from this site, not from another website.
                </>,
                <>
                  <Code>tf_oauth</Code>: exists for at most 10 minutes while you sign in with Google or GitHub.
                </>,
              ]}
            />
          </Section>

          <Section title="How it is used">
            <List
              items={[
                "Only to run the service: signing you in and showing you your own projects and data.",
                "Not sold, not shared with anyone, not used for advertising. Other users cannot see your projects.",
                "Diagnosis runs on this server with a built-in method; your data is not sent to an AI provider.",
                "Server logs record each request's method, path, status and duration, never request bodies, query strings or passwords. Failed sign-in attempts are counted briefly in memory to slow down password guessing.",
              ]}
            />
          </Section>

          <Section title="Where it is kept">
            <p>
              The site and its database are hosted by Render (render.com) in its Singapore region, over HTTPS only.
              Google or GitHub take part only when you choose to sign in with them, under their own privacy policies.
            </p>
            <p>
              This is a demonstration deployment on a free hosting plan: its database may be reset, so do not keep data
              here that you cannot afford to lose.
            </p>
          </Section>

          <Section title="Your choices">
            <List
              items={[
                "Delete datasets and revoke API keys in the console at any time.",
                <>
                  To have a project, or your account and everything in it, deleted, or to get a copy of your data, email{" "}
                  {mail}. Deleting a project deletes its traces, datasets, evaluations, diagnoses and experiments.
                </>,
                "You can remove Traceforge's access at any time in your Google account (Security → Third-party connections) or GitHub settings (Applications → Authorized OAuth Apps).",
              ]}
            />
          </Section>

          <Section title="Changes">
            <p>If this policy changes, this page is updated and the date at the top changes with it.</p>
          </Section>
        </main>

        <footer className="border-t border-border bg-app/70 px-4 py-4 backdrop-blur sm:px-6">
          <AuthorCredit className="mx-auto max-w-6xl" />
        </footer>
      </div>
    </div>
  );
}
