import { useMemo } from "react";
import { Link } from "react-router-dom";
import { ArrowRight, CheckCircle2, HardDriveDownload, PlusCircle, RadioTower, Server, Trash2, WandSparkles } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { PageHeader, StatChip } from "@/components/common/bits";
import { ACCENT } from "@/lib/accents";
import { TRANSPORTS, useSources } from "@/lib/source-context";

export default function Sources() {
  const { sources, removeSource, loadDemo, sourceType } = useSources();

  const totals = useMemo(() => {
    let accepted = 0;
    let rejected = 0;
    let rescued = 0;
    let lines = 0;
    for (const s of sources) {
      if (s.lastRun) {
        accepted += s.lastRun.accepted;
        rejected += s.lastRun.rejected;
        rescued += s.lastRun.rescued;
        lines += s.lastRun.total;
      }
    }
    return { accepted, rejected, rescued, lines };
  }, [sources]);

  return (
    <>
      <PageHeader
        eyebrow="Universal preprocessing layer"
        title="Log Sources"
        subtitle="Every collector, appliance, platform and custom app lands here, tags its events with source.name, and flows through the same multi-parser → OCSF pipeline. Raw stays raw; only the shape unifies."
      >
        <Button size="sm" variant="outline" onClick={loadDemo}>
          <WandSparkles className="h-3.5 w-3.5" />
          Load demo sources
        </Button>
        <Button asChild size="sm" className="bg-gradient-to-r from-[#2f8ce0] to-[#7c4dcc] text-white shadow-lg shadow-[#2f8ce0]/25">
          <Link to="/sources/new">
            <PlusCircle className="h-3.5 w-3.5" />
            Add log source
          </Link>
        </Button>
      </PageHeader>

      <div className="mb-5 grid grid-cols-2 gap-2 sm:grid-cols-4">
        <StatChip icon={Server} label="Sources" value={sources.length} color={ACCENT.blue} tone="text-[#25263A]" />
        <StatChip icon={CheckCircle2} label="Events (last runs)" value={totals.accepted} color="#2f8ce0" tone={totals.accepted ? "text-emerald-600" : "text-[#A6AABF]"} />
        <StatChip icon={RadioTower} label="Rescued by fallback" value={totals.rescued} color="#7c4dcc" tone={totals.rescued ? "text-[#25263A]" : "text-[#A6AABF]"} />
        <StatChip icon={HardDriveDownload} label="DLQ lines" value={totals.rejected} color="#e11d48" tone={totals.rejected ? "text-rose-600" : "text-[#A6AABF]"} />
      </div>

      {sources.length === 0 && (
        <div className="flex flex-col items-start gap-3 rounded-xl border border-dashed border-border bg-card/50 px-6 py-10">
          <span className="flex h-11 w-11 items-center justify-center rounded-xl text-white shadow-lg" style={{ backgroundColor: ACCENT.blue, boxShadow: `0 12px 24px -12px ${ACCENT.blue}` }}>
            <Server className="h-5 w-5" />
          </span>
          <p className="max-w-xl text-sm text-muted-foreground">
            No sources registered yet. Add your first collector, or load the three demo sources to see the full source → parser → DLQ story in seconds.
          </p>
          <div className="flex flex-wrap gap-2">
            <Button asChild size="sm" className="bg-gradient-to-r from-[#2f8ce0] to-[#7c4dcc] text-white shadow-lg shadow-[#2f8ce0]/25">
              <Link to="/sources/new">
                <PlusCircle className="h-3.5 w-3.5" />
                Add your first source
              </Link>
            </Button>
            <Button size="sm" variant="outline" onClick={loadDemo}>
              <WandSparkles className="h-3.5 w-3.5" />
              Load demo sources
            </Button>
          </div>
        </div>
      )}

      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {sources.map((s) => {
          const type = sourceType(s);
          const transport = TRANSPORTS.find((t) => t.id === s.transport);
          const Icon = type?.icon ?? Server;
          const color = type?.color ?? ACCENT.blue;
          return (
            <Card key={s.id} className="group gap-2 overflow-hidden bg-card/60 transition-all hover:-translate-y-0.5 hover:shadow-lg hover:shadow-black/5">
              <CardContent className="p-4">
                <div className="flex items-start justify-between gap-2">
                  <div className="flex items-center gap-2.5">
                    <span className="flex h-10 w-10 items-center justify-center rounded-xl text-white shadow-lg transition-transform group-hover:scale-105" style={{ backgroundColor: color, boxShadow: `0 10px 20px -10px ${color}` }}>
                      <Icon className="h-4.5 w-4.5" />
                    </span>
                    <div className="min-w-0">
                      <Link to={`/sources/${s.id}`} className="truncate text-sm font-bold hover:underline">
                        {s.name}
                      </Link>
                      <p className="text-[11px] text-muted-foreground">
                        {type?.label} · {transport?.label}
                      </p>
                    </div>
                  </div>
                  <span className="inline-flex items-center gap-1 rounded-full bg-[#BDEDE3] px-2 py-0.5 text-[10px] font-bold text-[#0f766e]">
                    <span className="h-1.5 w-1.5 rounded-full bg-[#0f766e]" />
                    Active
                  </span>
                </div>

                <div className="mt-3 flex flex-wrap items-center gap-1.5">
                  <Badge variant="outline" className="font-mono text-[10px]">
                    {s.format}
                  </Badge>
                  <Badge variant="outline" className="font-mono text-[10px]">{s.endpoint}</Badge>
                  <span className="ml-auto font-mono text-[10px] text-muted-foreground">
                    {s.createdAt ? new Date(s.createdAt).toLocaleDateString() : ""}
                  </span>
                </div>

                {s.lastRun ? (
                  <div className="mt-3 grid grid-cols-3 gap-1.5">
                    <MiniStat label="events" value={s.lastRun.accepted} tone={`bg-[#E8F4FF] text-[#2f8ce0]`} />
                    <MiniStat label="rescued" value={s.lastRun.rescued} tone={`bg-[#F0ECFF] text-[#7c4dcc]`} />
                    <MiniStat label="dlq" value={s.lastRun.rejected} tone={`bg-[#FFE1E6] text-[#e11d48]`} />
                  </div>
                ) : (
                  <p className="mt-3 text-[11px] text-muted-foreground">No ingestion from this source yet.</p>
                )}

                <div className="mt-3 flex items-center justify-between gap-2 border-t border-border pt-3">
                  <Link to={`/sources/${s.id}`} className="flex items-center gap-1 text-xs font-medium hover:underline" style={{ color }}>
                    Open source view <ArrowRight className="h-3 w-3" />
                  </Link>
                  <Button variant="ghost" size="sm" className="h-7 gap-1 text-xs text-muted-foreground hover:text-rose-600" onClick={() => removeSource(s.id)}>
                    <Trash2 className="h-3 w-3" />
                    Remove
                  </Button>
                </div>
              </CardContent>
            </Card>
          );
        })}
      </div>
    </>
  );
}

function MiniStat({ label, value, tone }: { label: string; value: number; tone: string }) {
  return (
    <div className={`flex flex-col items-center rounded-lg py-1.5 ${tone}`}>
      <span className="font-mono text-sm font-bold">{value}</span>
      <span className="font-mono text-[9px] uppercase tracking-wider opacity-80">{label}</span>
    </div>
  );
}