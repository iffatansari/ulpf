import { useMemo, useState } from "react";
import { Layers, Search } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { PageHeader } from "@/components/common/bits";
import { ACTION, CLASS_LIST, DISPOSITION, SEVERITY, STATUS, OCSF_VERSION } from "@/lib/ocsf";
import { categoryTone } from "@/lib/accents";
import { Link } from "react-router-dom";
import { ArrowRight } from "lucide-react";

function EnumTable({ title, map }: { title: string; map: Record<number, string> }) {
  const rows = Object.entries(map).map(([id, name]) => ({ id: Number(id), name }));
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-card">
      <div className="border-b border-border bg-muted/50 px-3 py-1.5 font-mono text-[10px] font-bold uppercase tracking-wider text-muted-foreground">{title}</div>
      <div className="grid grid-cols-2 gap-x-3 gap-y-1 px-3 py-2 sm:grid-cols-3">
        {rows.map((r) => (
          <span key={r.id} className="font-mono text-[11px]">
            <span className="text-[#2f8ce0]">{r.id}</span> <span className="text-muted-foreground">·</span> {r.name}
          </span>
        ))}
      </div>
    </div>
  );
}

export default function OcsfSchema() {
  const [q, setQ] = useState("");
  const filtered = useMemo(() => {
    const query = q.trim().toLowerCase();
    if (!query) return CLASS_LIST;
    return CLASS_LIST.filter((c) => c.class_name.toLowerCase().includes(query) || c.category_name.toLowerCase().includes(query) || Object.values(c.activities).some((a) => a.toLowerCase().includes(query)));
  }, [q]);

  const categories = useMemo(() => {
    const m = new Map<number, { name: string; classes: typeof CLASS_LIST }>();
    filtered.forEach((c) => {
      const entry = m.get(c.category_uid) ?? { name: c.category_name, classes: [] };
      entry.classes.push(c);
      m.set(c.category_uid, entry);
    });
    return [...m.entries()].sort((a, b) => a[0] - b[0]);
  }, [filtered]);

  return (
    <>
      <PageHeader
        eyebrow="Schema"
        title={`OCSF Schema · v${OCSF_VERSION}`}
        subtitle="The published OCSF 1.3.0 class and enum tables ULPF maps against. Every class_uid, category_uid, activity_id and enum above comes from this reference."
      >
        <div className="relative">
          <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search classes / activities…" className="h-8 w-64 pl-8 font-mono text-xs" />
        </div>
      </PageHeader>

      <div className="space-y-5">
        {categories.map(([uid, { name, classes }]) => {
          const tone = categoryTone(uid);
          return (
          <div key={uid}>
            <div className="mb-2 flex items-center gap-2">
              <span className="flex h-7 w-7 items-center justify-center rounded-lg text-white" style={{ backgroundColor: tone.strong, boxShadow: `0 8px 16px -8px ${tone.strong}` }}>
                <Layers className="h-3.5 w-3.5" />
              </span>
              <h2 className="text-base font-bold tracking-tight">{name}</h2>
              <Badge className="px-1.5 py-0.5 font-mono text-[10px] font-bold" style={{ backgroundColor: tone.tint, color: tone.strong, borderColor: tone.tint }}>
                category {uid}
              </Badge>
              <span className="text-xs text-muted-foreground">({classes.length})</span>
            </div>
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
              {classes.map((c) => (
                <Card key={c.class_uid} className="flex flex-col bg-card/60" style={{ borderTop: `3px solid ${tone.strong}` }}>
                  <CardHeader className="p-4 pb-2">
                    <div className="flex items-center justify-between gap-2">
                      <CardTitle className="text-sm">{c.class_name}</CardTitle>
                      <Badge variant="secondary" className="font-mono text-[10px]">
                        uid {c.class_uid}
                      </Badge>
                    </div>
                    <CardDescription className="font-mono text-[10px]">
                      type_uid range {c.class_uid * 100}–{c.class_uid * 100 + 99}
                    </CardDescription>
                  </CardHeader>
                  <CardContent className="mt-auto flex flex-wrap gap-1 p-4 pt-1">
                    {Object.entries(c.activities).map(([id, name]) => (
                      <Badge key={id} variant="outline" className="font-mono text-[10px] font-normal">
                        {id}: {name}
                      </Badge>
                    ))}
                  </CardContent>
                </Card>
              ))}
            </div>
          </div>
          );
        })}
        {filtered.length === 0 && <p className="rounded-lg border border-dashed border-border px-4 py-8 text-center text-sm text-muted-foreground">No classes match “{q}”.</p>}
      </div>

      <div className="mt-10">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-base font-bold tracking-tight">Enum references</h2>
          <Link to="/schema/explorer" className="flex items-center gap-1 text-xs font-medium text-[#2f8ce0] hover:underline">
            Explore attributes <ArrowRight className="h-3 w-3" />
          </Link>
        </div>
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <EnumTable title="severity_id" map={SEVERITY} />
          <EnumTable title="status_id" map={STATUS} />
          <EnumTable title="action_id" map={ACTION} />
          <EnumTable title="disposition_id" map={DISPOSITION} />
        </div>
      </div>
    </>
  );
}