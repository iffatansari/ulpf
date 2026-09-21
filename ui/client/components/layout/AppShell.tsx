import { useState } from "react";
import { NavLink, Outlet, Link, useLocation } from "react-router-dom";
import { Menu, ShieldCheck, Sparkles, UploadCloud, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { NAV_SECTIONS, FLAT_NAV, NavItem } from "./nav";
import { Button } from "@/components/ui/button";
import { ACCENT } from "@/lib/accents";

function Brand() {
  return (
    <Link to="/" className="flex items-center gap-3 px-1">
      <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl text-[#25263A] shadow-lg transition-transform hover:scale-105" style={{ background: "linear-gradient(135deg, #91C8FF 0%, #B9A7FF 55%, #BDEDE3 100%)", boxShadow: "0 8px 20px -8px rgba(57,118,255,0.45)" }}>
        <ShieldCheck className="h-5 w-5" />
      </div>
      <div className="min-w-0">
        <p className="truncate text-sm font-extrabold leading-none tracking-tight text-sidebar-foreground">ULPF</p>
        <p className="mt-1 flex items-center gap-1 font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
          <Sparkles className="h-3 w-3 text-[#7c4dcc]" />
          universal preprocessing layer
        </p>
      </div>
    </Link>
  );
}

function SidebarLink({ item, onNavigate }: { item: NavItem; onNavigate?: () => void }) {
  return (
    <NavLink
      to={item.path}
      end={item.path === "/"}
      onClick={onNavigate}
      className={({ isActive }) =>
        cn(
          "flex items-center gap-2.5 rounded-lg border px-2.5 py-2 text-[13px] font-semibold transition-all",
          isActive ? "shadow-sm" : "border-transparent text-sidebar-foreground/75 hover:bg-[#F2F5FA] hover:text-sidebar-foreground",
        )
      }
      style={({ isActive }) => (isActive ? { backgroundColor: item.tint, color: item.color, borderColor: item.tint } : undefined)}
    >
      {({ isActive }) => (
        <>
          <span
            className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-white transition-colors"
            style={isActive ? { backgroundColor: item.color } : { backgroundColor: "#ECF0F8", color: item.color }}
          >
            <item.icon className="h-3.5 w-3.5" />
          </span>
          <span className="truncate">{item.label}</span>
        </>
      )}
    </NavLink>
  );
}

function NavBody({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <nav className="flex-1 space-y-5 overflow-y-auto px-3 py-5">
      {NAV_SECTIONS.map((section) => (
        <div key={section.label}>
          <p className="mb-1.5 flex items-center gap-1.5 px-1 font-mono text-[10px] font-bold uppercase tracking-[0.18em] text-muted-foreground/70">
            <span className="h-1.5 w-1.5 rounded-full" style={{ backgroundColor: section.items[0].color }} />
            {section.label}
          </p>
          <div className="space-y-1">
            {section.items.map((item) => (
              <SidebarLink key={item.path} item={item} onNavigate={onNavigate} />
            ))}
          </div>
        </div>
      ))}
    </nav>
  );
}

export default function AppShell() {
  const [mobileOpen, setMobileOpen] = useState(false);
  const { pathname } = useLocation();
  const active = FLAT_NAV.find((i) => i.path === pathname);
  const section = NAV_SECTIONS.find((s) => s.items.some((i) => i.path === pathname))?.label ?? "Workspace";

  const side = (
    <>
      <div className="flex h-[72px] items-center border-b border-sidebar-border px-4">
        <Brand />
      </div>
      <NavBody onNavigate={() => setMobileOpen(false)} />
      <div className="mx-3 mb-3 rounded-xl border border-sidebar-border bg-gradient-to-br from-[#91C8FF]/20 via-[#B9A7FF]/15 to-[#BDEDE3]/25 p-3">
        <p className="flex items-center gap-1.5 text-xs font-bold text-sidebar-foreground">
          <span className="h-2 w-2 rounded-full bg-emerald-500" />
          Pipeline online
        </p>
        <p className="mt-1 font-mono text-[10px] text-muted-foreground">Express + Vite · single port</p>
      </div>
    </>
  );

  return (
    <div className="flex h-screen overflow-hidden bg-background text-foreground">
      {/* Desktop sidebar */}
      <aside className="hidden w-64 shrink-0 flex-col border-r border-sidebar-border bg-sidebar lg:flex">{side}</aside>

      {/* Mobile sidebar */}
      {mobileOpen && (
        <div className="fixed inset-0 z-50 lg:hidden">
          <div className="absolute inset-0 bg-[#25263A]/30 backdrop-blur-sm" onClick={() => setMobileOpen(false)} />
          <aside className="absolute inset-y-0 left-0 flex w-72 flex-col border-r border-sidebar-border bg-sidebar shadow-2xl">{side}</aside>
        </div>
      )}

      {/* Main column */}
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="h-1 shrink-0 bg-[linear-gradient(90deg,#91C8FF,10%,#B9A7FF,45%,#BDEDE3,75%,#FBBF24)]" />
        <header className="sticky top-0 z-30 border-b border-border bg-background/90 backdrop-blur">
          <div className="flex h-16 items-center justify-between gap-3 px-4 sm:px-6">
            <div className="flex min-w-0 items-center gap-3">
              <button onClick={() => setMobileOpen(true)} className="text-muted-foreground lg:hidden" aria-label="Open navigation">
                <Menu className="h-5 w-5" />
              </button>
              <div className="flex min-w-0 items-center gap-2.5">
                {active && (
                  <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-white" style={{ backgroundColor: active.color }}>
                    <active.icon className="h-4 w-4" />
                  </span>
                )}
                <div className="min-w-0">
                  <p className="truncate text-sm font-bold tracking-tight">{active?.label ?? "Workspace"}</p>
                  <p className="font-mono text-[10px] font-bold uppercase tracking-[0.16em]" style={{ color: active?.color ?? ACCENT.blue }}>
                    {section}
                  </p>
                </div>
              </div>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <span className="hidden items-center gap-1.5 rounded-full border border-emerald-200 bg-emerald-50 px-2.5 py-1 font-mono text-[10px] font-semibold text-emerald-700 sm:flex">
                <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-500" />
                api online
              </span>
              <Button asChild size="sm" className="bg-gradient-to-r from-[#2f8ce0] to-[#7c4dcc] text-white hover:from-[#2a7fd1] hover:to-[#6f44bd] shadow-lg shadow-[#2f8ce0]/25">
                <Link to="/events/ingest">
                  <UploadCloud className="h-3.5 w-3.5" />
                  New ingestion
                </Link>
              </Button>
            </div>
          </div>
        </header>

        <main className="min-w-0 flex-1 overflow-y-auto">
          <div className="mx-auto w-full max-w-6xl px-4 pb-16 pt-7 sm:px-6">
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  );
}