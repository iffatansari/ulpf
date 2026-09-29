import { useEffect, useMemo, useRef, useState } from "react";
import type {
  NormalizedEventResult,
  RejectedEventResult,
} from "@shared/api";
import {
  backendDlqToRejected,
  backendEventToLineResult,
  getBackendStats,
  listBackendDlq,
  listBackendEvents,
  mergeBackendEvents,
  subscribeToBackendEvents,
  type BackendDlqRecord,
  type BackendNormalizedEvent,
  type BackendStats,
  type SourcePerSourceStats,
} from "./backend";
import { isEvent, isRejected } from "./guards";
import { useNormalizer } from "./normalize-context";

export type FeedStreamState = "connecting" | "live" | "reconnecting";

/** `null` means the metric is not observable outside a local ingestion run. */
export type FeedMetric = number | null;

export interface FeedSummary {
  /** True when the numbers come from a local run, false from the backend pipeline. */
  fromLocalRun: boolean;
  sourceFormat: string;
  totalLines: FeedMetric;
  events: number;
  rejected: number;
  skipped: FeedMetric;
  rescued: FeedMetric;
  classes: number;
  formats: number;
  /** OCSF class -> count. Present in both modes so every page renders the same breakdown. */
  byClass: Record<string, number>;
  /** Detected format -> count. Present in both modes so every page renders the same breakdown. */
  byFormat: Record<string, number>;
  /** OCSF severity -> count, so the Metrics severity chart agrees with the chip. */
  bySeverity: Record<string, number>;
  /** parser_id -> count. */
  byParser: Record<string, number>;
  /** parser_tier -> count. */
  byTier: Record<string, number>;
  /** Raw events in Bronze, i.e. the pipeline's "input lines". */
  rawEvents: number;
  /** DLQ split: records no replay has recovered yet, and those that have. */
  dlqUnresolved: number;
  dlqRecovered: number;
  /** DLQ rejection classification -> count. */
  dlqReasons: Record<string, number>;
  uploads: number;
  registeredSources: number;
  reprocessRuns: number;
  /** Per source_id, derived from the events rather than the source registry. */
  perSource: SourcePerSourceStats[];
}

export interface NormalizedFeed {
  summary: FeedSummary;
  events: NormalizedEventResult[];
  rejected: RejectedEventResult[];
  streamState: FeedStreamState;
  lastRunAt: number | null;
  /** True once either a local run or a backend snapshot has produced data. */
  hasData: boolean;
}

const FEED_LIMIT = 50;
const DLQ_LIMIT = 200;

/**
 * How often the aggregate is re-read.
 *
 * The simulator publishes one record every two seconds, so a slower tick
 * than that makes the counters visibly lag the stream they are supposed to
 * be describing, and a faster one only adds load. Events arriving on the
 * SSE trigger a throttled re-read immediately, so this is the ceiling
 * rather than the cadence.
 */
const STATS_POLL_MS = 2000;
const STATS_MIN_REFRESH_MS = 1000;

const EMPTY_SUMMARY: FeedSummary = {
  fromLocalRun: false,
  sourceFormat: "backend · live pipeline",
  totalLines: 0,
  events: 0,
  rejected: 0,
  skipped: null,
  rescued: 0,
  classes: 0,
  formats: 0,
  byClass: {},
  byFormat: {},
  bySeverity: {},
  byParser: {},
  byTier: {},
  rawEvents: 0,
  dlqUnresolved: 0,
  dlqRecovered: 0,
  dlqReasons: {},
  uploads: 0,
  registeredSources: 0,
  reprocessRuns: 0,
  perSource: [],
};

function localRunSummary(result: NonNullable<ReturnType<typeof useNormalizer>["result"]>): FeedSummary {
  return {
    ...EMPTY_SUMMARY,
    fromLocalRun: true,
    sourceFormat: result.summary.source_format,
    totalLines: result.summary.total_lines,
    events: result.summary.events,
    rejected: result.summary.rejected,
    skipped: result.summary.skipped,
    rescued: result.summary.rescued,
    classes: Object.keys(result.summary.by_class).length,
    formats: Object.keys(result.summary.by_format).length,
    byClass: result.summary.by_class,
    byFormat: result.summary.by_format,
  };
}

/**
 * Single source of truth for every counter in the UI.
 *
 * The Dashboard, Metrics, Normalized events, Sources and DLQ pages all read
 * this hook, so their numbers are the same numbers by construction rather
 * than by each page happening to query OpenSearch the same way. Two
 * consequences worth stating:
 *
 *  - Counts come from the API's aggregate, not from tallies over the
 *    50-row event list. Tallying a page of rows and labelling the result
 *    "total" is how a breakdown chart ended up describing a sample while
 *    the chip above it claimed to describe everything.
 *  - The SSE stream is used for the event list only. It is a live feed,
 *    not a counter, and letting it drive the totals is what let the feed
 *    and the index disagree with each other mid-run.
 */
export function useNormalizedFeed(): NormalizedFeed {
  const { result, ranAt } = useNormalizer();
  const [stats, setStats] = useState<BackendStats | null>(null);
  const [live, setLive] = useState<BackendNormalizedEvent[] | null>(null);
  const [dlq, setDlq] = useState<BackendDlqRecord[] | null>(null);
  const [streamState, setStreamState] =
    useState<FeedStreamState>("connecting");

  const lastRefreshRef = useRef(0);

  useEffect(() => {
    let cancelled = false;
    let inflight = false;

    const loadStats = async () => {
      if (inflight) return;
      inflight = true;
      lastRefreshRef.current = Date.now();
      try {
        const data = await getBackendStats();
        if (!cancelled) setStats(data);
      } catch {
        // Keep the last good aggregate. A transient failure must not blank
        // the dashboard, and showing stale counts beats showing zeros.
      } finally {
        inflight = false;
      }
    };

    /** Coalesce bursts: at most one aggregate read per STATS_MIN_REFRESH_MS. */
    const requestStatsRefresh = () => {
      const since = Date.now() - lastRefreshRef.current;
      if (since < STATS_MIN_REFRESH_MS) return;
      void loadStats();
    };

    const loadSnapshot = () => {
      listBackendEvents(FEED_LIMIT)
        .then((data) => {
          if (cancelled) return;
          setLive(data.events);
        })
        .catch(() => undefined);
    };

    const loadDlq = () => {
      listBackendDlq(DLQ_LIMIT)
        .then((data) => {
          if (!cancelled) setDlq(data.records);
        })
        .catch(() => undefined);
    };

    const reloadAll = () => {
      void loadStats();
      loadSnapshot();
      loadDlq();
    };

    setStreamState("connecting");
    reloadAll();

    const closeStream = subscribeToBackendEvents(undefined, {
      onOpen: () => setStreamState("live"),
      onError: () => setStreamState("reconnecting"),
      onReset: reloadAll,
      onEvent: (event) => {
        setLive((previous) =>
          mergeBackendEvents(previous ?? [], [event], FEED_LIMIT),
        );
        requestStatsRefresh();
      },
    });

    const tick = setInterval(() => void loadStats(), STATS_POLL_MS);

    // Browsers throttle timers in a background tab, so a tab left open
    // across a reset shows the previous run's totals until it is focused.
    const onWake = () => {
      if (document.visibilityState === "visible") reloadAll();
    };
    document.addEventListener("visibilitychange", onWake);
    window.addEventListener("focus", onWake);

    return () => {
      cancelled = true;
      closeStream();
      clearInterval(tick);
      document.removeEventListener("visibilitychange", onWake);
      window.removeEventListener("focus", onWake);
    };
  }, []);

  const liveEvents = useMemo(
    () => (live ?? []).map((event, index) => backendEventToLineResult(event, index)),
    [live],
  );

  const events = useMemo(
    () => (result ? result.lines.filter(isEvent) : liveEvents),
    [result, liveEvents],
  );

  const rejected = useMemo<RejectedEventResult[]>(() => {
    if (result) return result.lines.filter(isRejected);
    return (dlq ?? []).map((record, index) => backendDlqToRejected(record, index));
  }, [result, dlq]);

  const summary = useMemo<FeedSummary>(() => {
    if (result) return localRunSummary(result);
    if (!stats) return EMPTY_SUMMARY;

    return {
      ...EMPTY_SUMMARY,
      // Bronze is the pipeline's input count: every raw event that entered
      // the pipeline, whether or not it normalized. Reporting it as "input
      // lines" is accurate, where the old `null` was just a missing number.
      totalLines: stats.bronze_events,
      events: stats.silver_events,
      rejected: stats.dlq_events,
      rescued: stats.rescued,
      classes: Object.keys(stats.classes).length,
      formats: Object.keys(stats.formats).length,
      byClass: stats.classes,
      byFormat: stats.formats,
      bySeverity: stats.severities,
      byParser: stats.parsers,
      byTier: stats.tiers,
      rawEvents: stats.bronze_events,
      dlqUnresolved: stats.dlq_unresolved,
      dlqRecovered: stats.dlq_recovered,
      dlqReasons: stats.dlq_reasons,
      uploads: stats.uploads,
      registeredSources: stats.registered_sources,
      reprocessRuns: stats.reprocess_runs,
      perSource: stats.per_source,
    };
  }, [result, stats]);

  return {
    summary,
    events,
    rejected,
    streamState,
    lastRunAt: ranAt,
    hasData: result !== null || stats !== null,
  };
}

/** Renders an unobservable metric as a dash rather than a fake zero. */
export function formatFeedMetric(value: FeedMetric): string {
  return value === null ? "—" : value.toLocaleString();
}
