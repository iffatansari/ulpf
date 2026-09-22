import { useMemo, useState } from "react";
import { toast } from "sonner";
import { CheckCircle2, GitBranch, Loader2, Wand2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { PageHeader } from "@/components/common/bits";
import { clusterDrainLogs } from "@/lib/backend";

const STEPS = ["Tokenize on delimiters", "Match longest common prefix", "Re-apply known templates", "Replace variable tokens"];

const DEFAULT_LOGS = `2024-01-22T12:42:48Z api-gateway INFO request from 203.0.113.9 to /login took 12ms
2024-01-22T12:42:49Z api-gateway INFO request from 203.0.113.9 to /login took 11ms
2024-01-22T12:42:50Z api-gateway INFO request from 198.51.100.4 to /login took 14ms
2024-01-22T12:42:52Z api-gateway ERROR request from 198.51.100.4 to /checkout took 331ms
2024-01-22T12:42:47Z worker-3  WARN queue depth 41 items for tenant 77
2024-01-22T12:42:53Z worker-3  WARN queue depth 38 items for tenant 77
2024-01-22T12:44:01Z worker-9  WARN queue depth 120 items for tenant 12
2024-01-22T12:45:00Z scheduler STARTED job cleanup-2024-01-22 hourly
the icing inspector visited a sleeping bear
==============================`;

interface Cluster {
  template: string;
  count: number;
  examples: string[];
}

export default function Drain3() {
  const [logs, setLogs] = useState(DEFAULT_LOGS);
  const [clusters, setClusters] = useState<Cluster[] | null>(null);
  const [busy, setBusy] = useState(false);

  const run = async () => {
    if (!logs.trim()) {
      toast.error("Paste some log lines first");
      return;
    }
    setBusy(true);
    try {
      setClusters(await clusterDrainLogs(logs));
    } catch {
      toast.error("Clustering failed");
    } finally {
      setBusy(false);
    }
  };

  const variableCount = useMemo(() => {
    if (!clusters) return 0;
    return clusters.reduce((total, c) => total + ((c.template.match(/<\*>/g) ?? []).length), 0);
  }, [clusters]);

  return (
    <>
      <PageHeader
        eyebrow="Parsers"
        title="Drain3 (Unsupervised)"
        subtitle="A small, faithful clone of IBM's Drain3 online log template mining. Deltas in a stream of logs are grouped into templates — the standard first step before building a structured parser."
      />

      <div className="mb-5 flex items-start gap-2.5 rounded-lg border border-border bg-card px-4 py-3 text-sm text-muted-foreground">
        <GitBranch className="mt-0.5 h-4 w-4 shrink-0 text-[#7c4dcc]" />
        <p>
          Drain works by tree matching: log lines are tokenized, common (constant) subtrees are shared between similar lines, and variable tokens (<span className="font-mono text-xs">numbers, IPs, UUIDs, timestamps</span>) collapse to
          placeholders. What remains is the log template — which you can hand over to a parser.
        </p>
      </div>

      <div className="mb-5 grid gap-2 sm:grid-cols-4">
        {STEPS.map((s, i) => (
          <Card key={s} className="bg-card/50">
            <CardHeader className="p-3.5">
              <CardTitle className="flex items-center gap-1.5 text-xs">
                <Badge variant="secondary" className="h-5 w-5 items-center justify-center rounded-full px-0 text-[10px]">
                  {i + 1}
                </Badge>
                {s}
              </CardTitle>
              <CardDescription className="text-[11px] leading-snug">Drain{` v0.5.1 semantics · depth 3 · max children 100`}</CardDescription>
            </CardHeader>
          </Card>
        ))}
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        <div>
          <div className="mb-2 flex items-center justify-between gap-2">
            <h2 className="text-base font-bold tracking-tight">Log stream</h2>
            <Button size="sm" onClick={run} disabled={busy}>
              {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Wand2 className="h-4 w-4" />}
              Cluster logs
            </Button>
          </div>
          <Textarea value={logs} onChange={(e) => setLogs(e.target.value)} className="min-h-[280px] resize-y font-mono text-xs leading-relaxed" spellCheck={false} />
          <p className="mt-2 font-mono text-[10px] text-muted-foreground">Noise lines (operator comments / separators) are ignored, mirroring Drain's pre-filter step.</p>
        </div>

        <div>
          <div className="mb-2 flex items-center justify-between gap-2">
            <h2 className="text-base font-bold tracking-tight">Extracted templates</h2>
            {clusters && (
              <Badge variant="outline" className="font-mono text-[11px]">
                {clusters.length} templates · {variableCount} params
              </Badge>
            )}
          </div>
          {!clusters ? (
            <div className="flex flex-col items-center gap-3 rounded-lg border border-dashed border-border px-6 py-12 text-center text-sm text-muted-foreground">
              <Wand2 className="h-6 w-6 text-[#2f8ce0]/70" />
              Run clustering to see constant templates with their variable tokens highlighted and grouped by frequency.
            </div>
          ) : clusters.length === 0 ? (
            <div className="flex flex-col items-center gap-3 rounded-lg border border-dashed border-border px-6 py-12 text-center text-sm text-muted-foreground">No usable log lines were found in the stream.</div>
          ) : (
            <div className="max-h-[420px] space-y-2 overflow-y-auto pr-1">
              {clusters.map((c) => (
                <div key={c.template} className="rounded-lg border border-border bg-card p-3">
                  <div className="flex items-center justify-between gap-2">
                    <Badge variant="secondary" className="font-mono text-[10px]">
                      {c.count} lines
                    </Badge>
                    <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500" />
                  </div>
                  <p className="mt-2 font-mono text-[11px] font-medium leading-relaxed text-[#25263A]">{c.template}</p>
                  {c.examples.length > 0 && (
                    <div className="mt-2 space-y-0.5 border-t border-border pt-2">
                      {c.examples.map((e) => (
                        <pre key={e} className="overflow-x-auto whitespace-pre-wrap break-words font-mono text-[10px] leading-relaxed text-muted-foreground">
                          {e}
                        </pre>
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </>
  );
}