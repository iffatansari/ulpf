import type { LucideIcon } from "lucide-react";
import { ArrowRightLeft, BarChart3, CircleAlert, FileCode2, GitBranch, HeartPulse, LayoutDashboard, PlusCircle, Puzzle, ScrollText, Search, Server, ShieldCheck, UploadCloud } from "lucide-react";
import { ACCENT } from "@/lib/accents";

export interface NavItem {
  label: string;
  path: string;
  icon: LucideIcon;
  color: string; // strong accent (icon / active text)
  tint: string; // soft fill
}

export interface NavSection {
  label: string;
  items: NavItem[];
}

export const NAV_SECTIONS: NavSection[] = [
  {
    label: "Dashboard",
    items: [{ label: "Dashboard", path: "/", icon: LayoutDashboard, color: ACCENT.blue, tint: ACCENT.blueMist }],
  },
  {
    label: "Events",
    items: [
      { label: "Ingest Events", path: "/events/ingest", icon: UploadCloud, color: ACCENT.blue, tint: ACCENT.blueMist },
      { label: "Normalized Events", path: "/events/normalized", icon: FileCode2, color: ACCENT.mint, tint: ACCENT.mintMist },
      { label: "DLQ (Failed Events)", path: "/events/dlq", icon: CircleAlert, color: ACCENT.rose, tint: ACCENT.roseMist },
    ],
  },
  {
    label: "Sources",
    items: [
      { label: "Log Sources", path: "/sources", icon: Server, color: ACCENT.blue, tint: ACCENT.blueMist },
      { label: "Add Source", path: "/sources/new", icon: PlusCircle, color: ACCENT.mint, tint: ACCENT.mintMist },
    ],
  },
  {
    label: "Parsers",
    items: [
      { label: "Multi-Parser Chain", path: "/parsers/configurations", icon: ArrowRightLeft, color: ACCENT.lilac, tint: ACCENT.lilacMist },
      { label: "Drain3 (Unsupervised)", path: "/parsers/drain3", icon: GitBranch, color: ACCENT.amber, tint: ACCENT.amberMist },
      { label: "Custom Parsers", path: "/parsers/custom", icon: Puzzle, color: ACCENT.blue, tint: ACCENT.blueMist },
    ],
  },
  {
    label: "Schema",
    items: [
      { label: "OCSF Schema", path: "/schema/ocsf", icon: ShieldCheck, color: ACCENT.lilac, tint: ACCENT.lilacMist },
      { label: "Schema Explorer", path: "/schema/explorer", icon: Search, color: ACCENT.blue, tint: ACCENT.blueMist },
    ],
  },
  {
    label: "Monitoring",
    items: [
      { label: "Metrics", path: "/monitoring/metrics", icon: BarChart3, color: ACCENT.amber, tint: ACCENT.amberMist },
      { label: "Logs", path: "/monitoring/logs", icon: ScrollText, color: ACCENT.blue, tint: ACCENT.blueMist },
      { label: "System Health", path: "/monitoring/health", icon: HeartPulse, color: ACCENT.emerald, tint: ACCENT.emeraldMist },
    ],
  },
];

export const FLAT_NAV: NavItem[] = NAV_SECTIONS.flatMap((s) => s.items);