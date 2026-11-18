import { Mail } from "lucide-react";
import { Link } from "react-router-dom";

import { cx } from "./ui";

export const AUTHOR = {
  name: "Hemanth Kumar",
  initials: "HK",
  role: "AI Engineer",
  education: "Computer Science and Engineering graduate, IIIT Surat",
  school: "Indian Institute of Information Technology, Surat",
  degree: "Computer Science and Engineering",
  phone: "+91 6301716863",
  phoneHref: "tel:+916301716863",
  email: "hk744610@gmail.com",
  linkedin: "https://www.linkedin.com/in/hemanth131104/",
  linkedinLabel: "linkedin.com/in/hemanth131104",
  github: "https://github.com/hemanth210205",
  githubLabel: "github.com/hemanth210205",
};

// lucide-react 1.x ships no brand marks, so these two are inline SVGs.
export function GitHubMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 16 16" fill="currentColor" aria-hidden className={className}>
      <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z" />
    </svg>
  );
}

export function LinkedInMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 16 16" fill="currentColor" aria-hidden className={className}>
      <path d="M0 1.146C0 .513.526 0 1.175 0h13.65C15.474 0 16 .513 16 1.146v13.708c0 .633-.526 1.146-1.175 1.146H1.175C.526 16 0 15.487 0 14.854V1.146zm4.943 12.248V6.169H2.542v7.225h2.401zm-1.2-8.212c.837 0 1.358-.554 1.358-1.248-.015-.709-.52-1.248-1.342-1.248-.822 0-1.359.54-1.359 1.248 0 .694.521 1.248 1.327 1.248h.016zm4.908 8.212V9.359c0-.216.016-.432.08-.586.173-.431.568-.878 1.232-.878.869 0 1.216.662 1.216 1.634v3.865h2.401V9.25c0-2.22-1.184-3.252-2.764-3.252-1.274 0-1.845.7-2.165 1.193v.025h-.016a5.54 5.54 0 01.016-.025V6.169h-2.4c.03.678 0 7.225 0 7.225h2.4z" />
    </svg>
  );
}

const linkClass =
  "inline-flex items-center gap-1.5 rounded px-1.5 py-1 text-slate-400 transition-colors hover:bg-surface-hover hover:text-slate-100";

/** "Built by" line with contact links: page footer in the console and on the sign-in screen. */
export function AuthorCredit({ className }: { className?: string }) {
  return (
    <div className={cx("flex flex-wrap items-center justify-between gap-x-4 gap-y-2 text-xs text-slate-500", className)}>
      <p>
        Traceforge · built by{" "}
        <Link to="/author" className="font-medium text-slate-300 underline-offset-2 hover:text-accent-400 hover:underline">
          {AUTHOR.name}
        </Link>
      </p>
      <nav aria-label="Contact the author" className="-mx-1.5 flex flex-wrap items-center gap-1">
        <a className={linkClass} href={`mailto:${AUTHOR.email}`}>
          <Mail className="h-3.5 w-3.5" aria-hidden />
          <span>{AUTHOR.email}</span>
        </a>
        <a className={linkClass} href={AUTHOR.linkedin} target="_blank" rel="noopener noreferrer">
          <LinkedInMark className="h-3.5 w-3.5" />
          <span>LinkedIn</span>
        </a>
        <a className={linkClass} href={AUTHOR.github} target="_blank" rel="noopener noreferrer">
          <GitHubMark className="h-3.5 w-3.5" />
          <span>GitHub</span>
        </a>
      </nav>
    </div>
  );
}
