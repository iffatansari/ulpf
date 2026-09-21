import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowRight, PlayCircle, ScrollText } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { PageHeader } from "@/components/common/bits";
import { isEvent, isRejected } from "@/lib/guards";
import { categoryTone } from "@/lib/accents";
import { useNormalizer } from "@/lib/normalize-context";
import { cn } from "@/lib/utils";

type Tab = "all" | "events" | "rejected";

export default function Logs() {
  const { result, loading, runSample } = useNormalizer();
  const [tab, setTab] = useState<Tab>("all");
  const [q, setQ] = useState("");

  const views = useMemo(() => {
    const lines = result?.lines ?? [];
    const qq = q.trim().toLowerCase();
    return lines.filter((l) => {
      if (tab === "events" && !isEvent(l)) return false;
      if (tab === "rejected" && !isRejected(l)) return false;
      if (!qq) return true;
      const hay = `${l.line_number} ${isEvent(l) ? l.source_line : l.line ?? ""} ${isEvent(l) ? l.event.message ?? "" : ""} ${isEvent(l) ? l.format : l.reason}`.toLowerCase();
      return hay.includes(qq);
    });
  }, [result, tab, q]);

  if (!result) {
    return (
      <>
        <PageHeader eyebrow="Monitoring · Logs" title="Logs" subtitle="Raw lines exactly as received, in order, with the parser decision on each.">
          <Button size="sm" onClick={() => runSample()} disabled={loading}>
            <PlayCircle className="h-3.5 w-3.5" />
            {loading ? "Running…" : "Run sample bundle"}
          </Button>
        </PageHeader>
        <div className="flex flex-col items-start gap-3 rounded-lg border border-dashed border-border bg-card/50 px-5 py-10">
          <p className="text-sm text-muted-foreground">Nothing to stream yet. Ingest a bundle to populate the log stream.</p>
          <Button asChild size="sm">
            <Link to="/events/ingest">
              Go to ingest <ArrowRight className="h-3.5 w-3.5" />
            </Link>
          </Button>
        </div>
      </>
    );
  }

  const tabCount = (t: Tab) => {
    if (t === "all") return result.lines.length;
    if (t === "events") return result.summary.events;
    return result.summary.rejected;
  };

  return (
    <>
      <PageHeader
        eyebrow="Monitoring · Logs"
        title="Logs"
        subtitle={`Raw ingestion stream · ${result.summary.total_lines} lines received, in original order.`}
      >
        <ScrollText className="h-4 w-4 text-[#2f8ce0]" />
      </PageHeader>

      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div className="flex gap-1 rounded-md border border-border bg-muted/60 p-0.5">
          {(["all", "events", "rejected"] as Tab[]).map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={cn(
                "rounded px-2.5 py-1 text-xs font-medium transition-colors",
                tab === t ? "bg-card shadow-sm" : "text-muted-foreground",
                t === "rejected" ? "text-rose-600" : t === "events" ? "text-emerald-600" : "",
              )}
            >
              {t} ({tabCount(t)})
            </button>
          ))}
        </div>
        <div className="relative">
          <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filter raw lines / reasons…" className="h-8 w-64 pl-8 font-mono text-xs" />
          <span className="absolute left-2.5 top-1/2 -translate-y-1/2 font-mono text-[10px] text-muted-foreground">⌕</span>
        </div>
      </div>

      <div className="overflow-hidden rounded-lg border border-border bg-card">
        {views.length === 0 && <div className="px-4 py-8 text-sm text-muted-foreground">No lines match the current filter.</div>}
        {views.map((l) =>
          isEvent(l) ? (
            <div key={`e-${l.line_number}`} className="flex gap-3 border-b border-border px-3 py-2 last:border-0">
              <span className="w-10 shrink-0 font-mono text-[10px] text-muted-foreground">#{l.line_number}</span>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge className="px-1.5 py-0.5 text-[10px] font-bold" style={{ backgroundColor: categoryTone(l.event.category_uid).tint, color: categoryTone(l.event.category_uid).strong, borderColor: categoryTone(l.event.category_uid).tint }}>
                    {l.event.class_name ?? "Base Event"}
                  </Badge>
                  <Badge variant="secondary" className="font-mono text-[10px]">
                    {l.format}
                  </Badge>
                  <span className="font-mono text-[10px] text-muted-foreground">{l.parser}</span>
                </div>
                <pre className="mt-1 overflow-x-auto whitespace-pre-wrap break-words font-mono text-[11px] leading-relaxed text-muted-foreground">{l.source_line}</pre>
              </div>
            </div>
          ) : (
            <div key={`r-${l.line_number}`} className="flex gap-3 border-b border-border bg-rose-50/40 px-3 py-2 last:border-0">
              <span className="w-10 shrink-0 font-mono text-[10px] text-muted-foreground">#{l.line_number}</span>
              <div className="min-w-0 flex-1">
                <Badge variant="destructive" className="px-1.5 py-0.5 text-[10px]">
                  {l.reason}
                </Badge>
                {l.line && <pre className="mt-1 whitespace-pre-wrap break-words font-mono text-[11px] leading-relaxed text-rose-700/70">{l.line}</pre>}
              </div>
            </div>
          ),
        )}
      </div>
    </>
  );
}