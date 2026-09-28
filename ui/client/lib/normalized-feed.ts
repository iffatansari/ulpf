import { useEffect, useMemo, useState } from "react";
import type {
  NormalizedEventResult,
  RejectedEventResult,
} from "@shared/api";
import {
  backendDlqToRejected,
  backendEventToLineResult,
  listBackendDlq,
  listBackendEvents,
  mergeBackendEvents,
  subscribeToBackendEvents,
  type BackendDlqRecord,
  type BackendNormalizedEvent,
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

const EMPTY_SUMMARY: FeedSummary = {
  fromLocalRun: false,
  sourceFormat: "backend · live pipeline",
  totalLines: null,
  events: 0,
  rejected: 0,
  skipped: null,
  rescued: null,
  classes: 0,
  formats: 0,
};

/**
 * Single source of truth for normalized-event counts.
 *
 * The Dashboard and the Normalized Events page both read this hook, so their
 * totals can never drift apart. A local ingestion run always wins; without
 * one we fall back to the backend snapshot plus the live normalized-event
 * stream. Line-level metrics (input lines, skipped, rescued) have no backend
 * equivalent and are reported as `null` rather than a misleading zero.
 */
export function useNormalizedFeed(): NormalizedFeed {
  const { result, ranAt } = useNormalizer();
  const [live, setLive] = useState<{
    total: number;
    events: BackendNormalizedEvent[];
  } | null>(null);
  const [dlq, setDlq] = useState<{
    total: number;
    records: BackendDlqRecord[];
  } | null>(null);
  const [streamState, setStreamState] =
    useState<FeedStreamState>("connecting");

  useEffect(() => {
    let cancelled = false;

    const loadSnapshot = () => {
      listBackendEvents(FEED_LIMIT)
        .then((data) => {
          if (cancelled) return;
          setLive({ total: data.total, events: data.events });
        })
        .catch(() => undefined);
    };

    const loadDlq = () => {
      listBackendDlq(DLQ_LIMIT)
        .then((data) => {
          if (!cancelled) setDlq(data);
        })
        .catch(() => undefined);
    };

    setStreamState("connecting");
    loadSnapshot();
    loadDlq();

    const closeStream = subscribeToBackendEvents(undefined, {
      onOpen: () => setStreamState("live"),
      onError: () => setStreamState("reconnecting"),
      onReset: () => {
        loadSnapshot();
        loadDlq();
      },
      onEvent: (event) => {
        setLive((previous) => {
          if (!previous) return { total: 1, events: [event] };
          const alreadyPresent = previous.events.some(
            (current) => current.event_id === event.event_id,
          );
          return {
            total: alreadyPresent ? previous.total : previous.total + 1,
            events: mergeBackendEvents(previous.events, [event], FEED_LIMIT),
          };
        });
      },
    });

    return () => {
      cancelled = true;
      closeStream();
    };
  }, []);

  const liveEvents = useMemo(
    () =>
      live?.events.map((event, index) =>
        backendEventToLineResult(event, index),
      ) ?? [],
    [live],
  );

  const events = useMemo(
    () => (result ? result.lines.filter(isEvent) : liveEvents),
    [result, liveEvents],
  );

  const rejected = useMemo<RejectedEventResult[]>(() => {
    if (result) return result.lines.filter(isRejected);
    return (dlq?.records ?? []).map((record, index) =>
      backendDlqToRejected(record, index),
    );
  }, [result, dlq]);

  const summary = useMemo<FeedSummary>(() => {
    if (result) {
      return {
        fromLocalRun: true,
        sourceFormat: result.summary.source_format,
        totalLines: result.summary.total_lines,
        events: result.summary.events,
        rejected: result.summary.rejected,
        skipped: result.summary.skipped,
        rescued: result.summary.rescued,
        classes: Object.keys(result.summary.by_class).length,
        formats: Object.keys(result.summary.by_format).length,
      };
    }

    const liveEventsSnapshot = live?.events ?? [];
    return {
      ...EMPTY_SUMMARY,
      events: live?.total ?? 0,
      rejected: dlq?.total ?? 0,
      classes: new Set(
        liveEventsSnapshot.map((event) => event.class_name ?? "Base Event"),
      ).size,
      formats: new Set(
        liveEventsSnapshot.map((event) => event.parser_id),
      ).size,
    };
  }, [result, live, dlq]);

  return {
    summary,
    events,
    rejected,
    streamState,
    lastRunAt: ranAt,
    hasData: result !== null || live !== null,
  };
}

/** Renders an unobservable metric as a dash rather than a fake zero. */
export function formatFeedMetric(value: FeedMetric): string {
  return value === null ? "—" : value.toLocaleString();
}
