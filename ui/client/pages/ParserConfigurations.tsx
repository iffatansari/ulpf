import { useState } from "react";
import { Braces, CheckCircle2, Info } from "lucide-react";
import { Switch } from "@/components/ui/switch";
import { Badge } from "@/components/ui/badge";
import { PageHeader } from "@/components/common/bits";
import { PARSERS } from "@/lib/parser-meta";

const FORMAT_TONE: Record<string, string> = {
  json: "text-[#2f8ce0] bg-[#E8F4FF]",
  syslog: "text-[#2563c9] bg-[#E8F4FF]",
  cef: "text-[#7c4dcc] bg-[#F0ECFF]",
  leef: "text-[#7c4dcc] bg-[#F0ECFF]",
  keyvalue: "text-[#b45309] bg-[#fde9b8]",
  apache: "text-[#c2410c] bg-[#ffe4cc]",
  text: "text-[#72748A] bg-[#E4E8F2]",
};

export default function ParserConfigurations() {
  const [enabled, setEnabled] = useState<Record<string, boolean>>(() => Object.fromEntries(PARSERS.map((p) => [p.id, true])));
  const [priority, setPriority] = useState(() => Object.fromEntries(PARSERS.map((p, i) => [p.id, i])));

  const move = (id: string, dir: -1 | 1) => {
    setPriority((prev) => {
      const list = [...PARSERS].sort((a, b) => prev[a.id] - prev[b.id]);
      const idx = list.findIndex((p) => p.id === id);
      const swap = idx + dir;
      if (swap < 0 || swap >= list.length) return prev;
      const next = { ...prev };
      next[list[idx].id] = swap;
      next[list[swap].id] = idx;
      return next;
    });
  };

  const ordered = [...PARSERS].sort((a, b) => priority[a.id] - priority[b.id]);
  const active = ordered.filter((p) => enabled[p.id]).map((p) => p.id);

  return (
    <>
      <PageHeader
        eyebrow="Parsers"
        title="Parser configurations"
        subtitle="The built-in parser chain, in detection order. Disable a parser to force later formats, or reorder which format is attempted first."
      >
        <Badge variant="outline" className="font-mono text-[11px]">
          {active.length}/{PARSERS.length} active
        </Badge>
      </PageHeader>

      <div className="mb-5 flex items-start gap-2.5 rounded-lg border border-border bg-card px-4 py-3 text-sm text-muted-foreground">
        <Info className="mt-0.5 h-4 w-4 shrink-0 text-[#2f8ce0]" />
        <p>
          Detection order matters: <span className="font-mono text-xs">{active.join(" → ")}</span>. To disable a format entirely (e.g. force everything through key=value parsing), toggle it off — the rest of the pipeline keeps running. Mentioned
          as a roadmap item: persist this configuration to the server.
        </p>
      </div>

      <div className="grid gap-3 lg:grid-cols-2">
        {ordered.map((p, listIdx) => (
          <div key={p.id} className="flex flex-col rounded-lg border border-border bg-card p-4">
            <div className="flex items-start justify-between gap-3">
              <div className="flex items-center gap-2.5">
                <span className={`flex h-8 w-8 items-center justify-center rounded-md border border-border ${enabled[p.id] ? FORMAT_TONE[p.id] ?? "text-[#72748A] bg-[#E4E8F2]" : "text-[#B9C2D6] bg-[#F2F5FA]"}`}>
                  <Braces className="h-4 w-4" />
                </span>
                <div>
                  <p className="text-sm font-bold">{p.name}</p>
                  <p className="font-mono text-[10px] text-muted-foreground">
                    {p.priority} · index {listIdx}
                  </p>
                </div>
              </div>
              <Switch checked={enabled[p.id]} onCheckedChange={(v) => setEnabled((prev) => ({ ...prev, [p.id]: v }))} />
            </div>

            <p className="mt-3 text-xs leading-relaxed text-muted-foreground">{p.description}</p>

            <ul className="mt-3 space-y-1">
              {p.features.map((f) => (
                <li key={f} className="flex items-start gap-1.5 text-xs text-muted-foreground">
                  <CheckCircle2 className="mt-0.5 h-3 w-3 shrink-0 text-emerald-500" />
                  {f}
                </li>
              ))}
            </ul>

            <div className="mt-3 flex flex-wrap gap-1">
              {p.formats.map((f) => (
                <Badge key={f} variant="secondary" className="font-mono text-[10px]">
                  {f}
                </Badge>
              ))}
            </div>

            <div className="mt-3 rounded-md border border-border bg-muted/40 p-2.5">
              <pre className="overflow-x-auto whitespace-pre-wrap break-words font-mono text-[10px] leading-relaxed text-[#5A5C73]">{p.example}</pre>
            </div>

            <div className="mt-3 flex items-center gap-1.5">
              <button
                onClick={() => move(p.id, -1)}
                disabled={listIdx === 0}
                className="rounded-md border border-border bg-card px-2.5 py-1 text-xs font-medium text-muted-foreground transition-colors enabled:hover:border-primary/50 enabled:hover:text-primary disabled:opacity-40"
              >
                ↑ earlier
              </button>
              <button
                onClick={() => move(p.id, 1)}
                disabled={listIdx === ordered.length - 1}
                className="rounded-md border border-border bg-card px-2.5 py-1 text-xs font-medium text-muted-foreground transition-colors enabled:hover:border-primary/50 enabled:hover:text-primary disabled:opacity-40"
              >
                ↓ later
              </button>
            </div>
          </div>
        ))}
      </div>
    </>
  );
}