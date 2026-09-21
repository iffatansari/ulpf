import type { ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import { useLocation } from "react-router-dom";
import { cn } from "@/lib/utils";
import { FLAT_NAV } from "@/components/layout/nav";
import { ACCENT } from "@/lib/accents";

export function PageHeader({ eyebrow, title, subtitle, children }: { eyebrow: string; title: string; subtitle?: string; children?: ReactNode }) {
  const { pathname } = useLocation();
  const item = FLAT_NAV.find((i) => i.path === pathname);
  const Icon: LucideIcon | null = item?.icon ?? null;
  const color = item?.color ?? ACCENT.blue;
  const tint = item?.tint ?? ACCENT.blueMist;

  return (
    <div className="mb-7 flex flex-wrap items-center gap-4 sm:gap-5">
      {Icon && (
        <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl text-white shadow-lg" style={{ backgroundColor: color, boxShadow: `0 10px 24px -10px ${color}` }}>
          <Icon className="h-5.5 w-5.5" />
        </div>
      )}
      <div className="min-w-0 flex-1">
        <span className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 font-mono text-[10px] font-bold uppercase tracking-[0.16em]" style={{ backgroundColor: tint, color }}>
          {eyebrow}
        </span>
        <h1 className="mt-1.5 text-2xl font-extrabold tracking-tight sm:text-3xl">{title}</h1>
        {subtitle && <p className="mt-1.5 max-w-2xl text-sm text-muted-foreground">{subtitle}</p>}
      </div>
      {children && <div className="flex shrink-0 flex-wrap items-center gap-2">{children}</div>}
    </div>
  );
}

export function StatChip({
  label,
  value,
  tone,
  icon: Icon,
  color = ACCENT.blue,
}: {
  label: string;
  value: number | string;
  tone?: string;
  icon?: LucideIcon;
  color?: string;
}) {
  return (
    <div className="flex items-center gap-3 rounded-xl border border-border bg-card px-3 py-2.5 shadow-sm transition-shadow hover:shadow-md">
      {Icon && (
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-white" style={{ backgroundColor: color, boxShadow: `0 8px 16px -8px ${color}` }}>
          <Icon className="h-4 w-4" />
        </span>
      )}
      <div className="min-w-0">
        <p className="truncate font-mono text-[10px] uppercase tracking-wider text-muted-foreground">{label}</p>
        <p className={cn("truncate font-mono text-lg font-bold", tone ?? "text-[#25263A]")}>{typeof value === "number" ? value.toLocaleString() : value}</p>
      </div>
    </div>
  );
}

export const SEVERITY_TONE: Record<number, string> = {
  0: "bg-[#E4E8F2] text-[#72748A]",
  1: "bg-[#E8F4FF] text-[#2f8ce0]",
  2: "bg-[#BDEDE3] text-[#0f766e]",
  3: "bg-[#FDE9B8] text-[#b45309]",
  4: "bg-[#FFE1E6] text-[#e11d48]",
  5: "bg-[#FB7185] text-white",
  6: "bg-[#dc2626] text-white",
};

export const SEVERITY_COLOR: Record<number, string> = {
  0: ACCENT.gray,
  1: ACCENT.blue,
  2: ACCENT.mint,
  3: ACCENT.amber,
  4: ACCENT.rose,
  5: ACCENT.rose,
  6: "#dc2626",
};