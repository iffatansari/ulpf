"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const LINKS = [
  { href: "/", label: "Dashboard" },
  { href: "/sources", label: "Sources" },
  { href: "/events", label: "Events" },
  { href: "/dlq", label: "DLQ" },
];

export default function Nav() {
  const pathname = usePathname();

  const isActive = (href: string) =>
    href === "/" ? pathname === "/" : pathname.startsWith(href);

  return (
    <nav className="nav">
      <Link href="/" className="nav-brand">
        ULPF <small>Universal Log Pre-processing Framework</small>
      </Link>
      <div className="nav-links">
        {LINKS.map((l) => (
          <Link
            key={l.href}
            href={l.href}
            className={`nav-link${isActive(l.href) ? " active" : ""}`}
          >
            {l.label}
          </Link>
        ))}
      </div>
    </nav>
  );
}