import { useState, type FormEvent } from "react";
import { Link, Navigate, useLocation, useNavigate } from "react-router-dom";

import { ApiError } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { Button, Field, TextInput } from "../components/ui";

export function AuthPage({ mode }: { mode: "login" | "register" }) {
  const { user, login, register } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const next = (location.state as { from?: string } | null)?.from ?? "/";

  if (user) return <Navigate to={next} replace />;

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (mode === "login") await login(email, password);
      else await register(email, password, displayName);
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
    <div className="grid min-h-screen place-items-center bg-ink-50 px-4">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex items-center gap-2 font-semibold tracking-tight">
          <span className="grid h-6 w-6 place-items-center bg-ink-900 font-mono text-xs text-white">tf</span>
          Traceforge
        </div>
        <form onSubmit={submit} className="space-y-4 border border-ink-200 bg-white p-6">
          <h1 className="text-lg font-semibold">{mode === "login" ? "Sign in" : "Create an account"}</h1>
          {mode === "register" && (
            <Field label="Name">
              <TextInput value={displayName} onChange={(e) => setDisplayName(e.target.value)} required maxLength={100} autoComplete="name" />
            </Field>
          )}
          <Field label="Email">
            <TextInput type="email" value={email} onChange={(e) => setEmail(e.target.value)} required autoComplete="email" />
          </Field>
          <Field label="Password" hint={mode === "register" ? "At least 12 characters." : undefined}>
            <TextInput
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              minLength={mode === "register" ? 12 : 1}
              autoComplete={mode === "login" ? "current-password" : "new-password"}
            />
          </Field>
          {error && (
            <p role="alert" className="border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
              {error}
            </p>
          )}
          <Button variant="primary" type="submit" disabled={busy} className="w-full justify-center">
            {busy ? "Please wait…" : mode === "login" ? "Sign in" : "Create account"}
          </Button>
        </form>
        <p className="mt-4 text-center text-sm text-ink-500">
          {mode === "login" ? (
            <>
              No account? <Link className="text-ink-900 underline" to="/register">Create one</Link>
            </>
          ) : (
            <>
              Have an account? <Link className="text-ink-900 underline" to="/login">Sign in</Link>
            </>
          )}
        </p>
      </div>
    </div>
  );
}
