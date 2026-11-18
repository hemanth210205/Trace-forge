import { Link, NavLink } from "react-router-dom";
import { BookOpen } from "lucide-react";

import { useAuth } from "../auth/AuthContext";
import { BrandMark } from "./BrandMark";
import { cx } from "./ui";

const linkClass = "transition-colors hover:text-slate-50";

/**
 * Header for the public pages (landing, docs). On the landing page the section links scroll in place; elsewhere
 * they go back to the landing page's sections.
 */
export function PublicNav({ onLanding = false, mode = "login" }: { onLanding?: boolean; mode?: "login" | "register" }) {
  const { user } = useAuth();
  const section = (id: string) => (onLanding ? `#${id}` : `/login#${id}`);
  const action = user
    ? { to: "/", label: "Open console" }
    : mode === "login"
      ? { to: "/register", label: "Create account" }
      : { to: "/login", label: "Sign in" };

  return (
    <header className="sticky top-0 z-30 border-b border-border bg-app/80 backdrop-blur-md">
      <div className="mx-auto flex h-14 max-w-6xl items-center justify-between gap-4 px-4 sm:px-6">
        <Link to="/login" className="flex shrink-0 items-center gap-2.5">
          <BrandMark className="h-7 w-7" />
          <span className="font-mono text-sm font-semibold uppercase tracking-[0.14em] text-slate-50">Traceforge</span>
          <span className="hidden rounded-md border border-white/10 px-1.5 py-0.5 font-mono text-2xs text-accent-400 sm:inline">v0.1</span>
        </Link>
        <nav aria-label="Site" className="flex items-center gap-5 font-mono text-xs text-slate-400 md:gap-6">
          <a href={section("platform")} className={cx(linkClass, "hidden md:inline")}>Platform</a>
          <a href={section("how")} className={cx(linkClass, "hidden md:inline")}>How to use</a>
          <NavLink
            to="/docs"
            className={({ isActive }) => cx(linkClass, "inline-flex items-center gap-1.5", isActive && "text-accent-300")}
          >
            <BookOpen className="h-3.5 w-3.5" aria-hidden />
            Docs
          </NavLink>
          <Link to="/author" className={cx(linkClass, "hidden md:inline")}>Author</Link>
        </nav>
        <Link
          to={action.to}
          className="shrink-0 rounded-full border border-white/10 bg-white/[0.03] px-4 py-1.5 font-mono text-xs text-slate-200 transition-colors hover:border-accent-400/60 hover:text-accent-300"
        >
          {action.label}
        </Link>
      </div>
    </header>
  );
}
