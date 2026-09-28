import { useCallback, useEffect, useState } from "react";
import { Braces, CheckCircle2, Info, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { Switch } from "@/components/ui/switch";
import { Badge } from "@/components/ui/badge";
import { PageHeader } from "@/components/common/bits";
import {
  listBackendParsers,
  updateBackendParser,
  type BackendParserDoc,
} from "@/lib/backend";

/**
 * Parser chain control. The list comes from the backend registry, which is
 * seeded from the orchestrator's real chain, so this page can only ever show
 * parsers that exist. Toggles and reordering are PUTs against the registry --
 * there is no local-only state left behind if a save fails.
 */

const FORMAT_TONE: Record<string, string> = {
  json: "text-[#2f8ce0] bg-[#E8F4FF]",
  syslog: "text-[#2563c9] bg-[#E8F4FF]",
  cef: "text-[#7c4dcc] bg-[#F0ECFF]",
  "drain3-fallback-v1": "text-[#72748A] bg-[#E4E8F2]",
};

const FEATURES: Record<string, string[]> = {
  "cef-parser-v1": [
    "Vendor / product header captured",
    "CEF severity 0-10 mapped to OCSF severity",
    "Extension keys parsed with quoted-value support",
  ],
  "json-parser-v1": [
    "Balanced-brace coalescing of multi-line objects",
    "(timestamp|src_ip|user) style aliases",
    "Unknown keys preserved under unmapped",
  ],
  "syslog-parser-v1": [
    "RFC 5424 structured data parsed and flattened, quote-aware",
    "NILVALUE handled, MSGID and PROCID captured",
    "RFC 3164 timestamps get an explicit year in UTC",
  ],
  "drain3-fallback-v1": [
    "Template clustering via the drain3 library",
    "Accepts any line as a template, so it will claim lines the others reject",
    "Runs last, so a claimed line is a false negative upstream",
  ],
};

const EXAMPLES: Record<string, string> = {
  "cef-parser-v1":
    'CEF:0|Palo Alto Networks|PA-VM|11.0|TRAFFIC|Allow outbound connection|4|src=10.0.0.41 dst=10.0.0.12 dpt=443 act=allow',
  "json-parser-v1":
    '{"@timestamp":"2024-01-22T12:42:48Z","src_ip":"10.0.0.9","msg":"connection reset"}',
  "syslog-parser-v1":
    '<34>1 2024-01-22T12:42:48Z web1 sshd 2321 - - "Failed password for admin"',
  "drain3-fallback-v1":
    "2024-01-22T12:42:48Z api-gateway login for user bob failed: bad password",
};

const DESCRIPTIONS: Record<string, string> = {
  "cef-parser-v1":
    "ArcSight CEF: header CEF:Version|Vendor|Product|Version|Signature|Severity|Name plus a key=value extension.",
  "json-parser-v1":
    "One object per line, or coalesced pretty-printed objects. Field names map to OCSF attributes via alias tables.",
  "syslog-parser-v1":
    "Supports RFC 5424 (<PRI>1 TIMESTAMP HOST APP PID MSGID SD MSG) and legacy RFC 3164 (<PRI>Mmm dd hh:mm:ss host app[pid]: msg).",
  "drain3-fallback-v1":
    "Drain3 clusters log lines by template so variable parts (ids, paths, IPs) become fields.",
};

export default function ParserConfigurations() {
  const [parsers, setParsers] = useState<BackendParserDoc[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setParsers(await listBackendParsers(true));
      setError(null);
    } catch {
      setError("Could not reach the parser registry.");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const setStatus = async (parser: BackendParserDoc, active: boolean) => {
    setSaving(parser.parser_id);
    try {
      const updated = await updateBackendParser(parser.parser_id, {
        status: active ? "active" : "disabled",
      });
      setParsers((prev) =>
        prev ? prev.map((p) => (p.parser_id === parser.parser_id ? updated : p)) : prev,
      );
    } catch {
      toast.error(`Could not update ${parser.display_name}`);
    } finally {
      setSaving(null);
    }
  };

  // Reordering swaps priority values so the two neighbours trade places. The
  // registry is ordered by priority ascending, so a lower number is tried
  // earlier.
  const move = async (ordered: BackendParserDoc[], parserId: string, dir: -1 | 1) => {
    const idx = ordered.findIndex((p) => p.parser_id === parserId);
    const swap = idx + dir;
    if (swap < 0 || swap >= ordered.length) return;

    const a = ordered[idx];
    const b = ordered[swap];
    setSaving(parserId);
    try {
      const [updatedA, updatedB] = await Promise.all([
        updateBackendParser(a.parser_id, { priority: b.priority }),
        updateBackendParser(b.parser_id, { priority: a.priority }),
      ]);
      setParsers((prev) =>
        prev
          ? prev.map((p) => {
              if (p.parser_id === updatedA.parser_id) return updatedA;
              if (p.parser_id === updatedB.parser_id) return updatedB;
              return p;
            })
          : prev,
      );
    } catch {
      toast.error("Could not reorder the parser chain");
      void load();
    } finally {
      setSaving(null);
    }
  };

  if (error) {
    return (
      <>
        <PageHeader eyebrow="Parsers" title="Parser configurations" />
        <p className="text-sm text-muted-foreground">{error}</p>
      </>
    );
  }

  if (!parsers) {
    return (
      <>
        <PageHeader eyebrow="Parsers" title="Parser configurations" />
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          Loading the parser registry…
        </div>
      </>
    );
  }

  const ordered = [...parsers].sort(
    (a, b) => a.priority - b.priority || a.display_name.localeCompare(b.display_name),
  );
  const active = ordered.filter((p) => p.status === "active").map((p) => p.parser_id);

  return (
    <>
      <PageHeader
        eyebrow="Parsers"
        title="Parser configurations"
        subtitle="The built-in parser chain, in detection order. Disable a parser to force later formats, or reorder which format is attempted first."
      >
        <Badge variant="outline" className="font-mono text-[11px]">
          {active.length}/{ordered.length} active
        </Badge>
      </PageHeader>

      <div className="mb-5 flex items-start gap-2.5 rounded-lg border border-border bg-card px-4 py-3 text-sm text-muted-foreground">
        <Info className="mt-0.5 h-4 w-4 shrink-0 text-[#2f8ce0]" />
        <p>
          Detection order matters:{" "}
          <span className="font-mono text-xs">{active.join(" → ") || "none active"}</span>.
          Changes are saved to the parser registry on the API, so they survive a reload.
        </p>
      </div>

      <div className="grid gap-3 lg:grid-cols-2">
        {ordered.map((p, listIdx) => {
          const isActive = p.status === "active";
          const features = FEATURES[p.parser_id] ?? [];
          return (
            <div key={p.parser_id} className="flex flex-col rounded-lg border border-border bg-card p-4">
              <div className="flex items-start justify-between gap-3">
                <div className="flex items-center gap-2.5">
                  <span
                    className={`flex h-8 w-8 items-center justify-center rounded-md border border-border ${
                      isActive
                        ? FORMAT_TONE[p.parser_id] ?? "text-[#72748A] bg-[#E4E8F2]"
                        : "text-[#B9C2D6] bg-[#F2F5FA]"
                    }`}
                  >
                    <Braces className="h-4 w-4" />
                  </span>
                  <div>
                    <p className="text-sm font-bold">{p.display_name}</p>
                    <p className="font-mono text-[10px] text-muted-foreground">
                      {p.parser_id} · priority {p.priority} · index {listIdx}
                    </p>
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  {saving === p.parser_id && (
                    <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />
                  )}
                  <Switch
                    checked={isActive}
                    disabled={saving === p.parser_id}
                    onCheckedChange={(v) => void setStatus(p, v)}
                  />
                </div>
              </div>

              <p className="mt-3 text-xs leading-relaxed text-muted-foreground">
                {p.description ?? DESCRIPTIONS[p.parser_id] ?? ""}
              </p>

              <ul className="mt-3 space-y-1">
                {features.map((f) => (
                  <li
                    key={f}
                    className="flex items-start gap-1.5 text-xs text-muted-foreground"
                  >
                    <CheckCircle2 className="mt-0.5 h-3 w-3 shrink-0 text-emerald-500" />
                    {f}
                  </li>
                ))}
              </ul>

              <div className="mt-3 flex flex-wrap gap-1">
                {(p.source_formats ?? []).map((f) => (
                  <Badge key={f} variant="secondary" className="font-mono text-[10px]">
                    {f}
                  </Badge>
                ))}
              </div>

              {EXAMPLES[p.parser_id] && (
                <div className="mt-3 rounded-md border border-border bg-muted/40 p-2.5">
                  <pre className="overflow-x-auto whitespace-pre-wrap break-words font-mono text-[10px] leading-relaxed text-[#5A5C73]">
                    {EXAMPLES[p.parser_id]}
                  </pre>
                </div>
              )}

              {p.last_test && (
                <p className="mt-3 font-mono text-[10px] text-muted-foreground">
                  last test: {p.last_test.matched ? "matched" : "no match"}
                  {p.last_test.tested_at ? ` at ${p.last_test.tested_at}` : ""}
                </p>
              )}

              <div className="mt-3 flex items-center gap-1.5">
                <button
                  onClick={() => void move(ordered, p.parser_id, -1)}
                  disabled={listIdx === 0 || saving === p.parser_id}
                  className="rounded-md border border-border bg-card px-2.5 py-1 text-xs font-medium text-muted-foreground transition-colors enabled:hover:border-primary/50 enabled:hover:text-primary disabled:opacity-40"
                >
                  ↑ earlier
                </button>
                <button
                  onClick={() => void move(ordered, p.parser_id, 1)}
                  disabled={listIdx === ordered.length - 1 || saving === p.parser_id}
                  className="rounded-md border border-border bg-card px-2.5 py-1 text-xs font-medium text-muted-foreground transition-colors enabled:hover:border-primary/50 enabled:hover:text-primary disabled:opacity-40"
                >
                  ↓ later
                </button>
              </div>
            </div>
          );
        })}
      </div>
    </>
  );
}
