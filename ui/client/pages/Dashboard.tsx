import { useRef, useState } from "react";
import { useMemo } from "react";
import { Link } from "react-router-dom";
import { ArrowRight, BadgeCheck, Ban, Check, CheckCircle2, Copy, FileText, Info, Layers, PlayCircle, PlugZap, Radar, ShieldCheck } from "lucide-react";
import { Button } from "@/components/ui/button";
import { toast } from "sonner";
import NormalizerForm from "@/components/normalizer/NormalizerForm";
import EventTable from "@/components/events/EventTable";
import { PageHeader, StatChip } from "@/components/common/bits";
import { isEvent } from "@/lib/guards";
import { useNormalizer } from "@/lib/normalize-context";
import { ACCENT } from "@/lib/accents";

export default function Dashboard() {
  const { result, loading, ranAt, runSample } = useNormalizer();
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [copied, setCopied] = useState(false);
  const resultsRef = useRef<HTMLDivElement>(null);

  const events = useMemo(() => (result ? result.lines.filter(isEvent) : []), [result]);
  const summary = result?.summary;

  const copyAll = async () => {
    if (events.length === 0) return;
    try {
      await navigator.clipboard.writeText(JSON.stringify(events.map((l) => l.event), null, 2));
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      toast.error("Clipboard unavailable — copy from the JSON viewer instead");
    }
  };

  return (
    <>
      <PageHeader
        eyebrow="Command center"
        title="Dashboard"
        subtitle="One ingestion drives every page: paste logs anywhere, then explore normalized events, the DLQ, metrics and the schema."
      />

      <section className="relative overflow-hidden rounded-2xl border border-border bg-gradient-to-br from-[#91C8FF] via-[#B9A7FF] to-[#BDEDE3] p-6 shadow-lg shadow-[#91C8FF]/20 sm:p-8">
        <div className="pointer-events-none absolute -right-16 -top-20 h-56 w-56 rounded-full bg-white/40 blur-3xl" />
        <div className="pointer-events-none absolute -bottom-24 left-1/3 h-64 w-64 rounded-full bg-white/25 blur-3xl" />
        <div className="relative flex flex-wrap items-center justify-between gap-6">
          <div className="max-w-xl">
            <span className="inline-flex items-center gap-1.5 rounded-full bg-[#25263A]/90 px-2.5 py-1 font-mono text-[10px] font-bold uppercase tracking-[0.16em] text-white">
              <ShieldCheck className="h-3 w-3 text-[#BDEDE3]" />
              ULPF · real values only
            </span>
            <h1 className="mt-4 text-2xl font-extrabold leading-tight tracking-tight text-[#25263A] sm:text-3xl">
              Normalize any log into <span className="text-white drop-shadow-sm">verified OCSF events</span>
            </h1>
            <p className="mt-2 max-w-lg text-sm text-[#25263A]/70">
              Register any source — firewall, app, cloud, IAM, IoT — deploy its collector, then watch a multi-parser fallback chain turn raw logs into verified OCSF events. Noise goes to the DLQ, never into the feed.
            </p>
            <div className="mt-5 flex flex-wrap gap-2.5">
              <Button asChild size="sm" className="bg-[#25263A] text-white shadow-lg hover:bg-[#2f3250]">
                <Link to="/sources/new">
                  <PlugZap className="h-3.5 w-3.5" />
                  Add log source
                </Link>
              </Button>
              <Button
                size="sm"
                variant="outline"
                className="border-[#25263A]/25 bg-white/60 text-[#25263A] backdrop-blur hover:bg-white"
                onClick={async () => {
                  if (await runSample()) resultsRef.current?.scrollIntoView({ behavior: "smooth" });
                }}
              >
                <PlayCircle className="h-3.5 w-3.5" />
                Run sample
              </Button>
            </div>
          </div>
          <div className="grid w-full max-w-sm gap-2 sm:w-72">
            {[
              { icon: BadgeCheck, label: "Schema-verified enums", color: ACCENT.blue },
              { icon: Radar, label: "Auto-detect 7 log formats", color: ACCENT.lilac },
              { icon: Ban, label: "Noise rejected, never faked", color: ACCENT.mint },
            ].map((f) => (
              <div key={f.label} className="flex items-center gap-2.5 rounded-xl border border-white/50 bg-white/50 px-3 py-2 backdrop-blur-sm">
                <span className="flex h-7 w-7 items-center justify-center rounded-lg text-white" style={{ backgroundColor: f.color }}>
                  <f.icon className="h-3.5 w-3.5" />
                </span>
                <span className="text-xs font-semibold text-[#25263A]">{f.label}</span>
              </div>
            ))}
          </div>
        </div>
      </section>

      <div className="mt-5 grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
        <StatChip icon={FileText} label="Input lines" value={summary?.total_lines ?? 0} color={ACCENT.gray} tone={summary?.total_lines ? "text-[#25263A]" : "text-[#A6AABF]"} />
        <StatChip icon={CheckCircle2} label="Events" value={summary?.events ?? 0} color="#2f8ce0" tone={summary?.events ? "text-emerald-600" : "text-[#A6AABF]"} />
        <StatChip icon={Ban} label="Rejected" value={summary?.rejected ?? 0} color="#e11d48" tone={summary?.rejected ? "text-rose-600" : "text-[#A6AABF]"} />
        <StatChip icon={Info} label="Skipped" value={summary?.skipped ?? 0} color="#d97706" tone={summary?.skipped ? "text-amber-600" : "text-[#A6AABF]"} />
        <StatChip icon={Layers} label="Classes" value={summary ? Object.keys(summary.by_class).length : 0} color="#7c4dcc" tone={summary ? "text-[#25263A]" : "text-[#A6AABF]"} />
      </div>

      <div className="mt-6 grid gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <div>
          <h2 className="mb-2 text-base font-bold tracking-tight">Quick normalize</h2>
          <NormalizerForm compact onDone={() => resultsRef.current?.scrollIntoView({ behavior: "smooth" })} />
        </div>
        <div ref={resultsRef} className="min-w-0 scroll-mt-20">
          <div className="mb-2 flex items-center justify-between gap-2">
            <h2 className="text-base font-bold tracking-tight">
              Normalized events
              {result && <span className="ml-1 text-sm font-normal text-muted-foreground">({events.length})</span>}
            </h2>
            <div className="flex items-center gap-2">
              <Button variant="outline" size="sm" onClick={copyAll} disabled={events.length === 0} title="Copy all normalized events as OCSF JSON">
                {copied ? <Check className="h-3.5 w-3.5 text-emerald-500" /> : <Copy className="h-3.5 w-3.5" />}
                {copied ? "Copied" : "Copy JSON"}
              </Button>
              <Button asChild variant="outline" size="sm">
                <Link to="/events/normalized">
                  Open events <ArrowRight className="h-3.5 w-3.5" />
                </Link>
              </Button>
            </div>
          </div>
          {events.length > 0 ? (
            <>
              <EventTable events={events} selectedIndex={selectedIndex} onSelect={setSelectedIndex} heightClass="max-h-[480px]" emptyMessage="No events match." />
              {ranAt && <p className="mt-2 font-mono text-[10px] text-muted-foreground">last run · {new Date(ranAt).toLocaleString()}</p>}
            </>
          ) : result ? (
            <div className="flex flex-col items-start gap-3 rounded-xl border border-dashed border-border bg-card/60 px-5 py-8">
              <span className="flex h-10 w-10 items-center justify-center rounded-xl text-white" style={{ backgroundColor: "#e11d48", boxShadow: "0 10px 20px -10px #e11d48" }}>
                <Ban className="h-4.5 w-4.5" />
              </span>
              <p className="text-sm text-muted-foreground">This ingestion produced no normalized events — every line was rejected. Check the DLQ for the rejected lines.</p>
              <Button asChild size="sm">
                <Link to="/events/dlq">
                  Open DLQ <ArrowRight className="h-3.5 w-3.5" />
                </Link>
              </Button>
            </div>
          ) : (
            <div className="flex flex-col items-start gap-3 rounded-xl border border-dashed border-border bg-card/60 px-5 py-8">
              <span className="flex h-10 w-10 items-center justify-center rounded-xl text-white" style={{ backgroundColor: ACCENT.blue, boxShadow: `0 10px 20px -10px ${ACCENT.blue}` }}>
                <Info className="h-4.5 w-4.5" />
              </span>
              <p className="text-sm text-muted-foreground">No ingestion yet. Run the sample bundle or use the quick normalizer to populate the whole workspace.</p>
              <Button size="sm" onClick={() => runSample()} disabled={loading} className="bg-gradient-to-r from-[#2f8ce0] to-[#7c4dcc] text-white shadow-lg shadow-[#2f8ce0]/25">
                {loading ? "Running…" : "Run sample bundle"}
              </Button>
            </div>
          )}
        </div>
      </div>
    </>
  );
}