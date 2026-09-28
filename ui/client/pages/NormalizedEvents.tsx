import { useState } from "react";
import { Link } from "react-router-dom";
import {
  ArrowRight,
  Ban,
  CheckCircle2,
  FileCode2,
  Layers,
  PlayCircle,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { PageHeader, StatChip } from "@/components/common/bits";
import EventTable from "@/components/events/EventTable";
import JsonViewer from "@/components/events/JsonViewer";
import RejectedLines from "@/components/events/RejectedLines";
import { ACCENT } from "@/lib/accents";
import { useNormalizer } from "@/lib/normalize-context";
import { useNormalizedFeed } from "@/lib/normalized-feed";

export default function NormalizedEvents() {
  const { result, loading, runSample } = useNormalizer();
  const {
    summary,
    events: normalized,
    rejected,
    streamState,
    hasData,
  } = useNormalizedFeed();
  const [selectedIndex, setSelectedIndex] = useState(0);

  const selected =
    selectedIndex < normalized.length ? normalized[selectedIndex] : undefined;

  const timeLabel = (ev: {
    time: number;
    metadata?: { labels?: string[] };
  }) => {
    if (
      !ev.time ||
      (ev.metadata?.labels ?? []).includes("no_timestamp_in_source")
    )
      return "no timestamp in source";
    return new Date(ev.time).toISOString();
  };

  if (!hasData) {
    return (
      <>
        <PageHeader
          eyebrow="Events · Normalized"
          title="Normalized events"
          subtitle="Inspect the OCSF 1.3.0 events produced by the last ingestion."
        >
          <Button size="sm" onClick={() => runSample()} disabled={loading}>
            <PlayCircle className="h-3.5 w-3.5" />
            {loading ? "Running…" : "Run sample bundle"}
          </Button>
        </PageHeader>
        <div className="flex flex-col items-start gap-3 rounded-lg border border-dashed border-border bg-card/50 px-5 py-10">
          <p className="text-sm text-muted-foreground">
            No ingestion yet. Run a sample or normalize logs on the Ingest
            Events page first.
          </p>
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
        eyebrow="Events · Normalized"
        title="Normalized events"
        subtitle={`${normalized.length} schema-valid OCSF events from the last ingestion. Click a row to inspect its full payload.`}
      >
        <div className="flex items-center gap-2">
          <Badge variant="outline" className="font-mono text-[11px]">
            source: {summary.sourceFormat}
          </Badge>
          {!summary.fromLocalRun ? (
            <Badge
              variant={streamState === "live" ? "default" : "outline"}
              className="font-mono text-[11px]"
            >
              {streamState === "live"
                ? "Live"
                : streamState === "reconnecting"
                  ? "Reconnecting"
                  : "Connecting"}
            </Badge>
          ) : null}
          <Button
            variant="outline"
            size="sm"
            onClick={() => runSample()}
            disabled={loading}
          >
            <PlayCircle className="h-3.5 w-3.5" />
            Re-run sample
          </Button>
        </div>
      </PageHeader>

      <div className="mb-5 grid grid-cols-2 gap-2 sm:grid-cols-4">
        <StatChip
          icon={CheckCircle2}
          label="Events"
          value={summary.events}
          color="#2f8ce0"
          tone="text-emerald-600"
        />
        <StatChip
          icon={Layers}
          label="Classes"
          value={summary.classes}
          color="#7c4dcc"
          tone="text-[#25263A]"
        />
        <StatChip
          icon={FileCode2}
          label="Formats"
          value={summary.formats}
          color={ACCENT.mint}
          tone="text-[#25263A]"
        />
        <StatChip
          icon={Ban}
          label="Rejected"
          value={summary.rejected}
          color="#e11d48"
          tone="text-rose-600"
        />
      </div>

      <div className="grid gap-5">
        <section>
          <JsonViewer
            event={selected?.event ?? null}
            lineNumber={selected?.line_number}
            footnote={selected ? timeLabel(selected.event) : undefined}
          />
        </section>
        <section>
          <EventTable
            events={normalized}
            selectedIndex={selectedIndex}
            onSelect={setSelectedIndex}
            showFilter
          />
        </section>
        <section>
          <RejectedLines
            rejected={rejected}
            title="Rejected logs"
            emptyMessage="Every ingested line normalized cleanly — nothing was quarantined."
            totalCount={summary.rejected}
            maxHeightClass="max-h-[360px]"
          />
        </section>
      </div>
    </>
  );
}
