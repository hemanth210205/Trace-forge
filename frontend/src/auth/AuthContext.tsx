import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { api, ApiError, onUnauthorized, setCsrfToken } from "../api/client";
import type { AuthResponse, User } from "../api/types";

interface AuthValue {
  user: User | null;
  ready: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string, displayName: string, inviteCode?: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [ready, setReady] = useState(false);

  const accept = useCallback((auth: AuthResponse) => {
    setCsrfToken(auth.csrf_token);
    setUser(auth.user);
  }, []);

  useEffect(() => {
    // Restore an existing session (the cookie is HttpOnly; /auth/me returns its CSRF token).
    api<AuthResponse>("/auth/me")
      .then(accept)
      .catch(() => setUser(null))
      .finally(() => setReady(true));
    return onUnauthorized(() => {
      setCsrfToken(null);
      setUser(null);
    });
  }, [accept]);

  const value = useMemo<AuthValue>(
    () => ({
      user,
      ready,
      login: async (email, password) => accept(await api<AuthResponse>("/auth/login", { method: "POST", body: { email, password } })),
      register: async (email, password, displayName, inviteCode) =>
        accept(
          await api<AuthResponse>("/auth/register", {
            method: "POST",
            body: { email, password, display_name: displayName, ...(inviteCode ? { invite_code: inviteCode } : {}) },
          }),
        ),
      logout: async () => {
        try {
          await api("/auth/logout", { method: "POST" });
        } catch (error) {
          if (!(error instanceof ApiError) || error.status !== 401) throw error;
        } finally {
          setCsrfToken(null);
          setUser(null);
        }
      },
    }),
    [user, ready, accept],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside AuthProvider");
  return value;
}
