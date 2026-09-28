import { Link } from "react-router-dom";
import {
  ArrowRight,
  BadgeCheck,
  Ban,
  CheckCircle2,
  FileCode2,
  FileText,
  Info,
  Layers,
  LifeBuoy,
  PlayCircle,
  PlugZap,
  Radar,
  ShieldCheck,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { PageHeader, StatChip } from "@/components/common/bits";
import { useNormalizer } from "@/lib/normalize-context";
import { formatFeedMetric, useNormalizedFeed } from "@/lib/normalized-feed";
import { ACCENT } from "@/lib/accents";

export default function Dashboard() {
  const { loading, ranAt, runSample } = useNormalizer();
  const { summary } = useNormalizedFeed();

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
                onClick={() => void runSample()}
                disabled={loading}
              >
                <PlayCircle className="h-3.5 w-3.5" />
                {loading ? "Running…" : "Run sample"}
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

      <div className="mt-5 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-base font-bold tracking-tight">Normalization summary</h2>
          <p className="mt-0.5 text-sm text-muted-foreground">
            Counts mirror the Normalized events page — one run, one source of truth.
          </p>
        </div>
        <Button asChild variant="outline" size="sm">
          <Link to="/events/normalized">
            Open events <ArrowRight className="h-3.5 w-3.5" />
          </Link>
        </Button>
      </div>

      <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
        <StatChip
          icon={FileText}
          label="Input lines"
          value={formatFeedMetric(summary.totalLines)}
          color={ACCENT.gray}
          tone={summary.totalLines ? "text-[#25263A]" : "text-[#A6AABF]"}
        />
        <StatChip
          icon={CheckCircle2}
          label="Events normalized"
          value={summary.events}
          color="#2f8ce0"
          tone={summary.events ? "text-emerald-600" : "text-[#A6AABF]"}
        />
        <StatChip
          icon={Ban}
          label="Rejected"
          value={summary.rejected}
          color="#e11d48"
          tone={summary.rejected ? "text-rose-600" : "text-[#A6AABF]"}
        />
        <StatChip
          icon={Info}
          label="Skipped"
          value={formatFeedMetric(summary.skipped)}
          color="#d97706"
          tone={summary.skipped ? "text-amber-600" : "text-[#A6AABF]"}
        />
        <StatChip
          icon={LifeBuoy}
          label="Rescued"
          value={formatFeedMetric(summary.rescued)}
          color="#7c4dcc"
          tone={summary.rescued ? "text-[#25263A]" : "text-[#A6AABF]"}
        />
        <StatChip
          icon={Layers}
          label="Classes"
          value={summary.classes}
          color="#7c4dcc"
          tone={summary.classes ? "text-[#25263A]" : "text-[#A6AABF]"}
        />
        <StatChip
          icon={FileCode2}
          label="Formats"
          value={summary.formats}
          color="#0f766e"
          tone={summary.formats ? "text-[#25263A]" : "text-[#A6AABF]"}
        />
      </div>

      {ranAt && (
        <p className="mt-3 font-mono text-[10px] text-muted-foreground">
          last run · {new Date(ranAt).toLocaleString()}
        </p>
      )}
    </>
  );
}
