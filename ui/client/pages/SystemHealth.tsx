import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Activity, Braces, CheckCircle2, HeartPulse, Loader2, ShieldCheck, XCircle } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { PageHeader, StatChip } from "@/components/common/bits";
import { OCSF_VERSION } from "@/lib/ocsf";
import { PARSERS } from "@/lib/parser-meta";
import { useNormalizer } from "@/lib/normalize-context";

export default function SystemHealth() {
  const { result, loading, runSample, ranAt } = useNormalizer();
  const [api, setApi] = useState<{ state: "checking" | "online" | "offline"; message?: string }>({ state: "checking" });
  const [lastCheck, setLastCheck] = useState<number | null>(null);

  const check = async (silent = false) => {
    try {
      const res = await fetch("/api/ping");
      const data = await res.json();
      setApi({ state: "online", message: data.message });
      setLastCheck(Date.now());
    } catch {
      setApi({ state: "offline", message: "unreachable" });
      if (!silent) toast.error("API ping failed — is the dev server running?");
    }
  };

  useEffect(() => {
    check();
    const id = setInterval(() => check(true), 15000);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const activeParsers = PARSERS.filter((p) => p.id !== "text").length;

  return (
    <>
      <PageHeader eyebrow="Monitoring · Health" title="System Health" subtitle="Liveness of the API, the parser chain and the schema the normalizer ships with.">
        <button
          onClick={() => check()}
          className="flex h-8 items-center gap-1.5 rounded-md border border-border bg-card px-3 text-xs font-medium text-muted-foreground transition-colors hover:border-primary/50 hover:text-primary"
        >
          <HeartPulse className="h-3.5 w-3.5" />
          Re-check
        </button>
      </PageHeader>

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <div className="rounded-lg border border-border bg-card px-3 py-2">
          <p className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">API · /api/ping</p>
          <div className="mt-1 flex items-center gap-1.5 font-mono text-lg font-bold">
            {api.state === "checking" ? (
              <><Loader2 className="h-4 w-4 animate-spin text-[#A6AABF]" /><span className="text-[#A6AABF]">checking</span></>
            ) : api.state === "online" ? (
              <><CheckCircle2 className="h-4 w-4 text-emerald-500" /><span className="text-emerald-600">online</span></>
            ) : (
              <><XCircle className="h-4 w-4 text-rose-500" /><span className="text-rose-600">offline</span></>
            )}
          </div>
          {api.message && <p className="font-mono text-[10px] text-muted-foreground">{api.message}</p>}
        </div>
        <StatChip icon={ShieldCheck} label="OCSF schema" value={OCSF_VERSION} color="#0f766e" tone="text-[#25263A]" />
        <StatChip icon={Braces} label="Parser modules" value={PARSERS.length} color="#2f8ce0" tone="text-[#25263A]" />
        <StatChip icon={Activity} label="Active formats" value={activeParsers} color="#d97706" tone="text-emerald-600" />
      </div>

      {lastCheck && (
        <p className="mt-2 font-mono text-[10px] text-muted-foreground">
          last check {new Date(lastCheck).toLocaleTimeString()} · auto-refresh 15s · {result ? `last ingestion ${ranAt ? new Date(ranAt).toLocaleTimeString() : ""}` : "no ingestion yet"}
        </p>
      )}

      <div className="mt-6 grid gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <div>
          <h2 className="mb-2 text-base font-bold tracking-tight">Parser chain readiness</h2>
          <div className="space-y-2">
            {PARSERS.map((p) => (
              <div key={p.id} className="flex items-center justify-between gap-3 rounded-lg border border-border bg-card px-4 py-2.5">
                <div className="flex min-w-0 items-center gap-2.5">
                  <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-500" />
                  <div className="min-w-0">
                    <p className="text-sm font-semibold">{p.name}</p>
                    <p className="truncate font-mono text-[10px] text-muted-foreground">
                      {p.formats.join(" · ")} · {p.priority}
                    </p>
                  </div>
                </div>
                <Badge variant="secondary" className="shrink-0 font-mono text-[10px]">
                  ready
                </Badge>
              </div>
            ))}
          </div>
        </div>

        <div className="space-y-4">
          <Card className="bg-card/60">
            <CardHeader className="p-4">
              <CardTitle className="flex items-center gap-2 text-sm">
                <ShieldCheck className="h-4 w-4 text-[#0f766e]" />
                Schema guarantees
              </CardTitle>
              <CardDescription>Invariants the pipeline enforces on every accepted event.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-2 p-4 pt-0">
              {[
                "type_uid = class_uid × 100 + activity_id — always in range",
                "enum ids restricted to published OCSF 1.3.0 values",
                "no fabricated values: severity 0 / status 0 when the line is silent",
                "fallback timestamps labelled no_timestamp_in_source",
                "unknown real fields kept under unmapped instead of dropped",
              ].map((g) => (
                <p key={g} className="flex items-start gap-2 text-xs text-muted-foreground">
                  <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-500" />
                  {g}
                </p>
              ))}
            </CardContent>
          </Card>

          <Card className="bg-card/60">
            <CardHeader className="p-4">
              <CardTitle className="flex items-center gap-2 text-sm">
                <Activity className="h-4 w-4 text-[#2f8ce0]" />
                API contract
              </CardTitle>
              <CardDescription>Transport limits for the single normalize endpoint.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-1.5 p-4 pt-0 font-mono text-[11px] text-muted-foreground">
              <p>POST /api/normalize · body limit 30 MB</p>
              <p>UI-side cap 20 MB per uploaded file</p>
              <p>route rejects content &gt; 25 M chars (413)</p>
              <p>dev port 8080 (single-port Vite + Express)</p>
              <p>prod port {`process.env.PORT || 3000`}</p>
            </CardContent>
          </Card>
        </div>
      </div>
    </>
  );
}