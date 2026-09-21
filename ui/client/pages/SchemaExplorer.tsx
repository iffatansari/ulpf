import { useMemo, useState } from "react";
import { Boxes, Search } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { PageHeader } from "@/components/common/bits";
import { CLASS_ATTRS, CLASS_LIST, OCSF_VERSION, OcsfClass } from "@/lib/ocsf";
import { cn } from "@/lib/utils";

export default function SchemaExplorer() {
  const [q, setQ] = useState("");
  const [selected, setSelected] = useState<OcsfClass>(CLASS_LIST[0]);
  const attrs = CLASS_ATTRS[selected.class_name] ?? [];

  const filtered = useMemo(() => {
    const query = q.trim().toLowerCase();
    if (!query) return CLASS_LIST;
    return CLASS_LIST.filter((c) => c.class_name.toLowerCase().includes(query) || c.category_name.toLowerCase().includes(query));
  }, [q]);

  const attributeSearch = q.trim().toLowerCase();

  return (
    <>
      <PageHeader
        eyebrow="Schema"
        title="Schema Explorer"
        subtitle={`Interactive attribute reference for the OCSF v${OCSF_VERSION} classes ULPF emits. Pick a class to see the fields it can populate and their meaning.`}
      />

      <div className="grid gap-5 lg:grid-cols-[280px_minmax(0,1fr)]">
        <aside>
          <div className="relative mb-2">
            <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search classes…" className="h-8 pl-8 font-mono text-xs" />
          </div>
          <div className="max-h-[70vh] space-y-0.5 overflow-y-auto pr-1">
            {filtered.map((c) => (
              <button
                key={c.class_uid}
                onClick={() => setSelected(c)}
                className={cn(
                  "flex w-full items-center justify-between gap-2 rounded-md border px-3 py-2 text-left text-xs font-medium transition-colors",
                  selected.class_uid === c.class_uid ? "border-primary/50 bg-accent text-accent-foreground" : "border-transparent text-muted-foreground hover:bg-accent/50",
                )}
              >
                <span className="truncate">{c.class_name}</span>
                <span className="shrink-0 font-mono text-[10px] text-muted-foreground">{c.class_uid}</span>
              </button>
            ))}
            {filtered.length === 0 && <p className="px-3 py-4 text-sm text-muted-foreground">No match.</p>}
          </div>
        </aside>

        <div className="min-w-0">
          <div className="rounded-lg border border-border bg-card p-4">
            <div className="flex flex-wrap items-center gap-2">
              <Badge className="px-2 py-0.5 text-[11px]">{selected.class_name}</Badge>
              <Badge variant="secondary" className="font-mono text-[10px]">
                class_uid {selected.class_uid}
              </Badge>
              <Badge variant="outline" className="font-mono text-[10px]">
                category_uid {selected.category_uid} · {selected.category_name}
              </Badge>
            </div>

            <div className="mt-3 flex flex-wrap gap-1">
              {Object.entries(selected.activities).map(([id, name]) => (
                <Badge key={id} variant="outline" className="font-mono text-[10px] font-normal">
                  {id}: {name}
                </Badge>
              ))}
            </div>
          </div>

          <div className="mt-4 overflow-hidden rounded-lg border border-border bg-card">
            <div className="grid grid-cols-[180px_110px_minmax(0,1fr)] border-b border-border bg-muted/50 px-3 py-1.5 font-mono text-[10px] font-bold uppercase tracking-wider text-muted-foreground">
              <span>attribute</span>
              <span>type</span>
              <span>description</span>
            </div>
            {attrs
              .filter((a) => !attributeSearch || a.name.toLowerCase().includes(attributeSearch) || a.description.toLowerCase().includes(attributeSearch))
              .map((a) => (
                <div key={a.name} className="grid grid-cols-[180px_110px_minmax(0,1fr)] gap-2 border-b border-border px-3 py-2 last:border-0">
                  <span className="break-words font-mono text-[11px] font-medium text-[#2f8ce0]">{a.name}</span>
                  <span className="font-mono text-[10px] uppercase tracking-wide text-muted-foreground">{a.type}</span>
                  <span className="text-xs leading-relaxed text-muted-foreground">{a.description}</span>
                </div>
              ))}
            {attrs.length === 0 && <p className="px-4 py-6 text-sm text-muted-foreground">No curated attributes for this class yet.</p>}
          </div>
        </div>
      </div>

      <div className="mt-8 flex items-start gap-2.5 rounded-lg border border-border bg-card px-4 py-3 text-sm text-muted-foreground">
        <Boxes className="mt-0.5 h-4 w-4 shrink-0 text-[#0f766e]" />
        <p>
          Every instance carries the Base Event attributes as well — <span className="font-mono text-xs">time, class_uid, category_uid, type_uid, severity_id, status_id, activity_id, metadata, message, unmapped, raw_data</span> — so a
          class below is the full contract once merged with those.
        </p>
      </div>
    </>
  );
}