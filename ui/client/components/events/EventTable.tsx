import { useMemo, useState } from "react";
import { ArrowDownLeft, ArrowUpRight, CircleAlert, Search } from "lucide-react";
import type { NormalizedEventResult } from "@shared/api";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { SEVERITY_TONE } from "@/components/common/bits";
import { categoryTone } from "@/lib/accents";
import { cn } from "@/lib/utils";

interface Props {
  events: NormalizedEventResult[];
  selectedIndex: number;
  onSelect: (index: number) => void;
  heightClass?: string;
  showFilter?: boolean;
  emptyMessage?: string;
}

export default function EventTable({ events, selectedIndex, onSelect, heightClass = "max-h-[56vh]", showFilter = false, emptyMessage }: Props) {
  const [filter, setFilter] = useState("");
  const q = filter.trim().toLowerCase();

  const visible = useMemo(() => {
    if (!q) return events;
    return events.filter((l) => {
      const ev = l.event;
      return JSON.stringify({
        message: ev.message,
        class_name: ev.class_name,
        type_name: ev.type_name,
        src: ev.src_endpoint?.ip,
        dst: ev.dst_endpoint?.ip,
        user: ev.user?.name,
        api: ev.api?.operation,
        query: ev.query?.hostname,
      })
        .toLowerCase()
        .includes(q);
    });
  }, [events, q]);

  return (
    <div>
      {showFilter && (
        <div className="relative mb-2">
          <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="Filter by message, ip, user, class, api…" className="h-8 pl-8 font-mono text-xs" />
        </div>
      )}
      <div className="overflow-hidden rounded-xl border border-border bg-card">
        <div className={`${heightClass} divide-y divide-border overflow-y-auto`}>
          {visible.length === 0 && (
            <div className="flex flex-col items-center gap-2 px-4 py-10 text-center text-sm text-muted-foreground">
              <CircleAlert className="h-5 w-5" />
              {emptyMessage ?? "No events to show."}
            </div>
          )}
          {visible.map((l) => {
            const ev = l.event;
            const idx = events.indexOf(l);
            const src = ev.src_endpoint?.ip;
            const dst = ev.dst_endpoint?.ip;
            const apiOp = ev.api?.operation ?? (ev.http_request?.url?.path ? `${ev.http_request.method ?? ""} ${ev.http_request.url.path}`.trim() : undefined);
            const tone = categoryTone(ev.category_uid);
            return (
              <button
                key={`${l.line_number}-${ev.class_uid}`}
                onClick={() => onSelect(idx)}
                className={cn(
                  "block w-full border-l-2 px-3 py-2.5 text-left transition-colors hover:bg-accent/60",
                  selectedIndex === idx ? "bg-accent/70" : "border-transparent",
                )}
                style={selectedIndex === idx ? { borderLeftColor: tone.strong } : undefined}
              >
                <div className="flex items-center gap-2">
                  <span className="w-10 shrink-0 font-mono text-[10px] text-muted-foreground">#{l.line_number}</span>
                  <Badge className="shrink-0 px-1.5 py-0.5 text-[10px] font-bold" style={{ backgroundColor: tone.tint, color: tone.strong, borderColor: tone.tint }}>
                    {ev.class_name ?? "Base Event"}
                  </Badge>
                  <span className={`shrink-0 rounded px-1.5 py-0.5 font-mono text-[10px] font-semibold ${SEVERITY_TONE[ev.severity_id ?? 0] ?? "bg-[#E4E8F2] text-[#72748A]"}`}>
                    {(ev.severity ?? "unknown").toUpperCase()}
                  </span>
                  {l.chain_rescued && (
                    <span
                      title={`parser fallback chain: ${(l.parser_chain ?? []).join(" → ")}`}
                      className="shrink-0 rounded bg-[#F0ECFF] px-1.5 py-0.5 font-mono text-[9px] font-bold uppercase tracking-wide text-[#7c4dcc]"
                    >
                      rescued
                    </span>
                  )}
                  {ev.status_id !== undefined && ev.status_id !== 0 && (
                    <span className="shrink-0 font-mono text-[10px] text-muted-foreground">{ev.status}</span>
                  )}
                  {src && (
                    <span className="hidden shrink-0 items-center gap-0.5 font-mono text-[10px] text-muted-foreground sm:inline-flex">
                      <ArrowUpRight className="h-3 w-3 text-[#2f8ce0]" />
                      {src}
                      <ArrowDownLeft className="h-3 w-3 text-[#7c4dcc]" />
                      {dst ?? "?"}
                    </span>
                  )}
                  {apiOp && <span className="hidden shrink-0 font-mono text-[10px] text-muted-foreground lg:inline">{apiOp}</span>}
                </div>
                <p className="mt-1 line-clamp-1 pl-12 text-xs text-muted-foreground">{ev.message ?? ev.activity_name ?? ev.type_name}</p>
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}