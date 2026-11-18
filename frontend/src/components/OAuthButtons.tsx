import { useState } from "react";
import { Link } from "react-router-dom";
import { Info } from "lucide-react";

import { api } from "../api/client";
import { useApi } from "../api/useApi";
import { GitHubMark } from "./AuthorCredit";

type Provider = "google" | "github";

function GoogleMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 48 48" aria-hidden className={className}>
      <path fill="#FFC107" d="M43.6 20.1H42V20H24v8h11.3c-1.6 4.7-6.1 8-11.3 8-6.6 0-12-5.4-12-12s5.4-12 12-12c3.1 0 5.8 1.2 8 3l5.7-5.7C34 6.1 29.3 4 24 4 13 4 4 13 4 24s9 20 20 20 20-9 20-20c0-1.3-.1-2.6-.4-3.9z" />
      <path fill="#FF3D00" d="M6.3 14.7l6.6 4.8C14.7 15.1 19 12 24 12c3.1 0 5.8 1.2 8 3l5.7-5.7C34 6.1 29.3 4 24 4 16.3 4 9.7 8.3 6.3 14.7z" />
      <path fill="#4CAF50" d="M24 44c5.2 0 9.9-2 13.4-5.2l-6.2-5.2A11.9 11.9 0 0124 36c-5.2 0-9.6-3.3-11.3-7.9l-6.5 5C9.5 39.6 16.2 44 24 44z" />
      <path fill="#1976D2" d="M43.6 20.1H42V20H24v8h11.3a12 12 0 01-4.1 5.6l6.2 5.2C37 39.2 44 34 44 24c0-1.3-.1-2.6-.4-3.9z" />
    </svg>
  );
}

const PROVIDERS: { id: Provider; label: string; icon: (p: { className?: string }) => JSX.Element }[] = [
  { id: "github", label: "GitHub", icon: GitHubMark },
  { id: "google", label: "Google", icon: GoogleMark },
];

const buttonClass =
  "flex h-11 w-full items-center justify-center gap-3 rounded-xl border border-white/15 bg-transparent text-sm font-medium text-slate-100 transition-colors hover:border-white/30 hover:bg-white/[0.04]";

/**
 * "or" divider plus stacked "Continue with GitHub / Google" buttons, shown under the email form.
 * A configured provider is a plain link to the backend's start route (the OAuth exchange happens server-side).
 * An unconfigured one looks the same but explains, when clicked, that the server has no credentials for it.
 */
export function OAuthButtons({ next }: { next: string }) {
  const configured = useApi((signal) => api<{ providers: Provider[] }>("/auth/providers", { signal }), []);
  const available = new Set(configured.data?.providers ?? []);
  const [notice, setNotice] = useState<string | null>(null);

  return (
    <div>
      <div className="my-5 flex items-center gap-3 text-xs text-slate-500">
        <span className="h-px flex-1 bg-white/[0.08]" />
        or
        <span className="h-px flex-1 bg-white/[0.08]" />
      </div>
      <div className="space-y-3">
        {PROVIDERS.map(({ id, label, icon: Icon }) =>
          available.has(id) ? (
            <a key={id} href={`/api/v1/auth/oauth/${id}/start?next=${encodeURIComponent(next)}`} className={buttonClass}>
              <Icon className="h-[18px] w-[18px]" />
              Continue with {label}
            </a>
          ) : (
            <button key={id} type="button" className={buttonClass} onClick={() => setNotice(label)}>
              <Icon className="h-[18px] w-[18px]" />
              Continue with {label}
            </button>
          ),
        )}
      </div>
      {notice && (
        <p role="status" className="mt-3 flex items-start gap-2 rounded-xl border border-pulse-400/25 bg-pulse-400/[0.06] px-3 py-2 text-xs text-slate-300">
          <Info className="mt-px h-3.5 w-3.5 shrink-0 text-pulse-400" aria-hidden />
          <span>
            {notice} sign-in isn&apos;t set up on this server yet.{" "}
            <Link to="/docs#oauth" className="font-medium text-pulse-300 hover:underline">
              How to enable it
            </Link>
          </span>
        </p>
      )}
    </div>
  );
}

/** Message for ``/login?oauth_error=<code>`` set by the backend when a provider sign-in fails. */
export function oauthErrorMessage(code: string | null): string | null {
  if (!code) return null;
  const messages: Record<string, string> = {
    not_configured: "That sign-in option is not set up on this server.",
    denied: "Sign-in was cancelled.",
    state_mismatch: "The sign-in link expired or was opened in another browser. Please try again.",
    state_expired: "The sign-in link expired. Please try again.",
    email_unverified: "That account has no verified email address. Verify it with the provider, then try again.",
    inactive: "This account is disabled.",
  };
  return messages[code] ?? "Could not complete sign-in with the provider. Please try again.";
}
