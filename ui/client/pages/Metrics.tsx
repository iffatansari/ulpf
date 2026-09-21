import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowRight, Ban, CheckCircle2, FileCode2, Layers, PlayCircle, TrendingUp } from "lucide-react";
import { CartesianGrid, Cell, ResponsiveContainer, Bar, BarChart, Legend, Pie, PieChart, Tooltip, XAxis, YAxis } from "recharts";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { PageHeader, StatChip } from "@/components/common/bits";
import { isEvent } from "@/lib/guards";
import { SEVERITY } from "@/lib/ocsf";
import { useNormalizer } from "@/lib/normalize-context";

const SEV_COLORS = ["#cbd5e1", "#7dd3fc", "#34d399", "#fbbf24", "#fb923c", "#f43f5e", "#dc2626"];
const CLASS_COLORS = ["#60a5fa", "#34d399", "#fbbf24", "#f472b6", "#a78bfa", "#22d3ee", "#f87171", "#a3e635", "#38bdf8", "#e879f9", "#facc15", "#2dd4bf"];

export default function Metrics() {
  const { result, loading, runSample } = useNormalizer();
  const [severityOf, setSeverityOf] = useState<"events" | "accepted">("accepted");

  const byClass = useMemo(() => {
    if (!result) return [];
    return Object.entries(result.summary.by_class)
      .sort((a, b) => b[1] - a[1])
      .map(([name, value], i) => ({ name, value, color: CLASS_COLORS[i % CLASS_COLORS.length] }));
  }, [result]);

  const byFormat = useMemo(() => {
    if (!result) return [];
    return Object.entries(result.summary.by_format)
      .sort((a, b) => b[1] - a[1])
      .map(([name, value]) => ({ name, value }));
  }, [result]);

  const severityData = useMemo(() => {
    if (!result) return [];
    const counts = new Map<string, number>();
    result.lines.forEach((l) => {
      if (!isEvent(l)) return;
      const sev = l.event.severity_id ?? 0;
      const label = SEVERITY[sev] ?? "Other";
      if (severityOf === "events" || sev > 0) counts.set(label, (counts.get(label) ?? 0) + 1);
    });
    return [...counts.entries()]
      .sort((a, b) => b[1] - a[1])
      .map(([name, value], i) => ({ name, value, color: SEV_COLORS[i % SEV_COLORS.length] }));
  }, [result, severityOf]);

  const accepted = result ? result.summary.events : 0;
  const rejected = result ? result.summary.rejected : 0;
  const outcomeData = useMemo(
    () => [
      { name: "Accepted", value: accepted, color: "#34d399" },
      { name: "Rejected", value: rejected, color: "#f43f5e" },
    ],
    [accepted, rejected],
  );

  if (!result) {
    return (
      <>
        <PageHeader eyebrow="Monitoring · Metrics" title="Metrics" subtitle="Ingestion statistics from the definition of done: real events in, noise quarantined.">
          <Button size="sm" onClick={() => runSample()} disabled={loading}>
            <PlayCircle className="h-3.5 w-3.5" />
            {loading ? "Running…" : "Run sample bundle"}
          </Button>
        </PageHeader>
        <div className="flex flex-col items-start gap-3 rounded-lg border border-dashed border-border bg-card/50 px-5 py-10">
          <p className="text-sm text-muted-foreground">No data yet. Run the sample bundle to populate the metric charts.</p>
          <Button asChild size="sm">
            <Link to="/events/ingest">
              Go to ingest <ArrowRight className="h-3.5 w-3.5" />
            </Link>
          </Button>
        </div>
      </>
    );
  }

  return (
    <>
      <PageHeader
        eyebrow="Monitoring · Metrics"
        title="Metrics"
        subtitle={`Distribution of the last ingestion · ${result.summary.total_lines.toLocaleString()} input lines.`}
      >
        <Badge variant="outline" className="font-mono text-[11px]">
          source: {result.summary.source_format}
        </Badge>
      </PageHeader>

      <div className="mb-5 grid grid-cols-2 gap-2 sm:grid-cols-4">
        <StatChip icon={CheckCircle2} label="Accepted" value={accepted} color="#2f8ce0" tone="text-emerald-600" />
        <StatChip icon={Ban} label="Rejected" value={rejected} color="#e11d48" tone="text-rose-600" />
        <StatChip icon={Layers} label="Classes" value={byClass.length} color="#7c4dcc" tone="text-[#25263A]" />
        <StatChip icon={FileCode2} label="Formats" value={byFormat.length} color="#0f766e" tone="text-[#25263A]" />
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        <div className="rounded-lg border border-border bg-card p-4">
          <h3 className="mb-1 flex items-center gap-2 text-sm font-bold">
            <TrendingUp className="h-4 w-4 text-[#2f8ce0]" />
            Events by class
          </h3>
          <p className="mb-3 text-xs text-muted-foreground">Counts from summary.by_class — the OCSF class_uid assigned to each accepted line.</p>
          <div className="h-64">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={byClass} layout="vertical" margin={{ left: 8, right: 16 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#eef2f7" horizontal={false} />
                <XAxis type="number" tick={{ fontSize: 11, fontFamily: "JetBrains Mono" }} />
                <YAxis type="category" dataKey="name" width={130} tick={{ fontSize: 11 }} />
                <Tooltip contentStyle={{ fontSize: 12, borderRadius: 8, border: "1px solid #e2e8f0" }} />
                <Bar dataKey="value" radius={[0, 6, 6, 0]}>
                  {byClass.map((d) => (
                    <Cell key={d.name} fill={d.color} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>

        <div className="rounded-lg border border-border bg-card p-4">
          <h3 className="mb-1 flex items-center gap-2 text-sm font-bold">
            <TrendingUp className="h-4 w-4 text-[#2f8ce0]" />
            Accepted vs rejected
          </h3>
          <p className="mb-3 text-xs text-muted-foreground">Real events that passed the parsers versus lines quarantined to the DLQ.</p>
          <div className="flex h-64 items-center justify-center gap-4">
            <div className="h-full w-1/2">
              <ResponsiveContainer width="100%" height="100%">
                <PieChart>
                  <Pie data={outcomeData} dataKey="value" nameKey="name" innerRadius={55} outerRadius={85} paddingAngle={3}>
                    {outcomeData.map((d) => (
                      <Cell key={d.name} fill={d.color} />
                    ))}
                  </Pie>
                  <Tooltip contentStyle={{ fontSize: 12, borderRadius: 8, border: "1px solid #e2e8f0" }} />
                  <Legend wrapperStyle={{ fontSize: 12 }} />
                </PieChart>
              </ResponsiveContainer>
            </div>
            <div className="w-auto space-y-3">
              {outcomeData.map((d) => (
                <div key={d.name}>
                  <p className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">{d.name}</p>
                  <p className="font-mono text-xl font-bold" style={{ color: d.color }}>
                    {d.value.toLocaleString()}
                  </p>
                </div>
              ))}
            </div>
          </div>
        </div>

        <div className="rounded-lg border border-border bg-card p-4">
          <div className="mb-1 flex items-center justify-between gap-2">
            <h3 className="flex items-center gap-2 text-sm font-bold">
              <TrendingUp className="h-4 w-4 text-[#2f8ce0]" />
              Severity by {severityOf === "accepted" ? "leveled events" : "all events"}
            </h3>
            <select
              value={severityOf}
              onChange={(e) => setSeverityOf(e.target.value as "events" | "accepted")}
              className="h-7 rounded-md border border-border bg-card px-2 font-mono text-[11px] text-muted-foreground"
            >
              <option value="accepted">{`only severity > 0`}</option>
              <option value="events">all events</option>
            </select>
          </div>
          <p className="mb-3 text-xs text-muted-foreground">severity_id assigned from the source level — Unknown (0) is never pretended to be a real level.</p>
          <div className="h-64">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={severityData} margin={{ left: -24, right: 16 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#eef2f7" vertical={false} />
                <XAxis dataKey="name" tick={{ fontSize: 10 }} />
                <YAxis tick={{ fontSize: 11, fontFamily: "JetBrains Mono" }} />
                <Tooltip contentStyle={{ fontSize: 12, borderRadius: 8, border: "1px solid #e2e8f0" }} />
                <Bar dataKey="value" radius={[6, 6, 0, 0]}>
                  {severityData.map((d) => (
                    <Cell key={d.name} fill={d.color} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>

        <div className="rounded-lg border border-border bg-card p-4">
          <h3 className="mb-1 flex items-center gap-2 text-sm font-bold">
            <TrendingUp className="h-4 w-4 text-[#2f8ce0]" />
            Lines by detected format
          </h3>
          <p className="mb-3 text-xs text-muted-foreground">How the auto-detector routed each line — note JSON pretty-printed blocks count once.</p>
          <div className="h-64">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={byFormat} margin={{ left: -24, right: 16 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#eef2f7" vertical={false} />
                <XAxis dataKey="name" tick={{ fontSize: 11 }} />
                <YAxis tick={{ fontSize: 11, fontFamily: "JetBrains Mono" }} />
                <Tooltip contentStyle={{ fontSize: 12, borderRadius: 8, border: "1px solid #e2e8f0" }} />
                <Bar dataKey="value" fill="#60a5fa" radius={[6, 6, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      </div>
    </>
  );
}