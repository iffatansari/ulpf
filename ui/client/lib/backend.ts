import type {
  NormalizedEventResult,
  OcsfEvent,
  RejectedEventResult,
} from "@shared/api";
import type { Source, SourceTransportId } from "./source-context";

/**
 * Typed client + adapters for the ULPF FastAPI backend, reached through
 * the same-origin /backend/* proxy on the UI's Express server
 * (see server/routes/backend.ts).
 *
 * Domain shapes here mirror the api/routes contracts on the Python side.
 */

// ---------------------------------------------------------------------------
// Backend contracts (mirrors api/routes)
// ---------------------------------------------------------------------------

export interface BackendSourceDoc {
  source_id: string;
  name: string;
  source_type: string;
  transport: string;
  expected_format: string;
  enabled: boolean;
  created_at: string;
  last_seen_at?: string | null;
  last_upload_at?: string | null;
  description?: string | null;
}

export interface SourceStats {
  source_id: string;
  raw_events: number;
  normalized_events: number;
  dlq_events: number;
  published_events: number;
  blank_lines: number;
  line_errors: number;
  formats: Record<string, number>;
  parsers: Record<string, number>;
  last_ingested_at?: string | null;
  success_rate: number;
}

export interface BackendNormalizedEvent {
  event_id: string;
  raw_event_id: string;
  parser_id: string;
  parser_tier: string;
  confidence_score: number;
  schema_id: string;
  schema_version: string;
  class_name?: string | null;
  category_name?: string | null;
  activity_name?: string | null;
  severity?: string | null;
  time: string | number;
  user?: string | null;
  device_id?: string | null;
  app_id?: string | null;
  src_endpoint?: string | null;
  dst_endpoint?: string | null;
  protocol_name?: string | null;
  action?: string | null;
  extensions?: Record<string, unknown>;
}

/**
 * One resolved replay attempt, appended to the DLQ record by the API and
 * never rewritten. `attempt` is derived server-side from the record's own
 * counter, so it stays correct across separate runs.
 */
export interface DlqAttemptEntry {
  attempt: number;
  reprocess_id?: string | null;
  started_at: string;
  result: "recovered" | "failed";
  reason?: string | null;
}

export interface BackendDlqRecord {
  dlq_id: string;
  raw_event_id: string;
  raw_payload?: string | null;
  parsers_attempted: string[];
  status: string;
  classification?: string | null;
  first_seen_at: string;
  last_attempt_at: string;
  reprocess_count: number;
  metadata?: Record<string, unknown>;
  // Module 3 recovery audit. `status` above is the ORIGINAL failure
  // classification and is never overwritten -- "was this event originally a
  // failure?" must stay answerable after a successful replay.
  resolution_status?: "unresolved" | "recovered";
  resolved_at?: string | null;
  last_reprocess_id?: string | null;
  replay_reason?: string | null;
  attempt_history?: DlqAttemptEntry[];
}

export interface BackendDashboard {
  sources: number;
  normalized_events: number;
  dlq_events: number;
  uploads: number;
  recent_events: BackendNormalizedEvent[];
  recent_uploads: Record<string, unknown>[];
}

// ---------------------------------------------------------------------------
// Low-level fetch through the /backend proxy
// ---------------------------------------------------------------------------

async function backendFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/backend${path}`, init);
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  const data = text ? (JSON.parse(text) as T) : (undefined as T);
  if (!res.ok) {
    const detail = (data as { detail?: string })?.detail;
    throw new Error(detail || `backend ${res.status}`);
  }
  return data;
}

function jsonInit(method: string, body?: unknown): RequestInit {
  return {
    method,
    headers: { "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  };
}

// ---------------------------------------------------------------------------
// Parser registry
//
// The registry on the API is the source of truth for which parsers exist and
// in what order they are tried. The UI never invents a parser list: the ids
// here are the ones the orchestrator really runs.
// ---------------------------------------------------------------------------

export type ParserStatus = "active" | "disabled" | "draft";

export interface BackendParserFieldRule {
  name: string;
  pattern: string;
}

export interface BackendParserDoc {
  parser_id: string;
  display_name: string;
  description?: string | null;
  status: ParserStatus;
  priority: number;
  version: number;
  is_builtin: boolean;
  source_formats?: string[];
  field_rules?: BackendParserFieldRule[];
  sample_payload?: string | null;
  history?: unknown[];
  last_test?: BackendParserTestResult | null;
  created_at?: string;
  updated_at?: string;
}

export interface BackendParserTestResult {
  parser_id: string;
  matched: boolean;
  /** Present for built-ins: the normalized event the real parser produced. */
  event?: Record<string, unknown> | null;
  /** Present for custom parsers: what the declarative field rules extracted. */
  extracted?: Record<string, string>;
  /**
   * Present for custom parsers: the rules that matched nothing. Without it a
   * rule that silently extracts nothing looks exactly like one that works.
   */
  misses?: string[];
  error?: string | null;
  /** Why a parser declined a sample, or what kind of result this is. */
  reason?: string | null;
  errors?: string[];
  tested_at?: string;
}

export async function listBackendParsers(
  includeDisabled = false,
): Promise<BackendParserDoc[]> {
  const data = await backendFetch<{ parsers: BackendParserDoc[] }>(
    `/parsers?include_disabled=${includeDisabled}`,
  );
  return data.parsers ?? [];
}

export async function getBackendParser(
  parserId: string,
): Promise<BackendParserDoc> {
  return backendFetch<BackendParserDoc>(`/parsers/${parserId}`);
}

export interface BackendParserCreate {
  parser_id: string;
  display_name: string;
  description?: string;
  status?: ParserStatus;
  priority?: number;
  source_formats?: string[];
  field_rules?: BackendParserFieldRule[];
  sample_payload?: string;
}

export async function createBackendParser(
  payload: BackendParserCreate,
): Promise<BackendParserDoc> {
  return backendFetch<BackendParserDoc>("/parsers", jsonInit("POST", payload));
}

export async function updateBackendParser(
  parserId: string,
  changes: Partial<Omit<BackendParserCreate, "parser_id">>,
): Promise<BackendParserDoc> {
  return backendFetch<BackendParserDoc>(
    `/parsers/${parserId}`,
    jsonInit("PUT", changes),
  );
}

export async function rollbackBackendParser(
  parserId: string,
): Promise<BackendParserDoc> {
  return backendFetch<BackendParserDoc>(
    `/parsers/${parserId}/rollback`,
    jsonInit("POST"),
  );
}

export async function deleteBackendParser(parserId: string): Promise<void> {
  await backendFetch<{ deleted: string }>(
    `/parsers/${parserId}`,
    jsonInit("DELETE"),
  );
}

/**
 * Run a sample through a real parser on the API. This is the whole point of
 * the endpoint: a built-in runs the orchestrator's own callable, so the
 * answer is what the pipeline would actually do.
 */
export async function testBackendParser(
  parserId: string,
  sample: string,
): Promise<BackendParserTestResult> {
  return backendFetch<BackendParserTestResult>(
    "/parsers/test",
    jsonInit("POST", { parser_id: parserId, sample }),
  );
}

// ---------------------------------------------------------------------------
// Sources registry
// ---------------------------------------------------------------------------

export async function listBackendSources(): Promise<BackendSourceDoc[]> {
  const data = await backendFetch<{ sources: BackendSourceDoc[] }>("/sources");
  return data.sources ?? [];
}

export interface BackendSourceCreate {
  name: string;
  source_type: string;
  transport: string;
  expected_format: "auto" | "mixed" | "syslog" | "json" | "cef";
  enabled: boolean;
  description?: string;
}

export async function createBackendSource(
  input: BackendSourceCreate,
): Promise<BackendSourceDoc> {
  const data = await backendFetch<{ source: BackendSourceDoc }>(
    "/sources",
    jsonInit("POST", input),
  );
  return data.source;
}

export async function updateBackendSource(
  sourceId: string,
  patch: Partial<
    Pick<
      BackendSourceDoc,
      "name" | "expected_format" | "enabled" | "description"
    >
  >,
): Promise<BackendSourceDoc> {
  const data = await backendFetch<{ source: BackendSourceDoc }>(
    `/sources/${sourceId}`,
    jsonInit("PUT", patch),
  );
  return data.source;
}

export async function deleteBackendSource(sourceId: string): Promise<void> {
  await backendFetch<unknown>(`/sources/${sourceId}`, { method: "DELETE" });
}

export async function getBackendSourceStats(
  sourceId: string,
): Promise<SourceStats> {
  return backendFetch<SourceStats>(
    `/sources/${encodeURIComponent(sourceId)}/stats`,
  );
}

// ---------------------------------------------------------------------------
// Events & DLQ
// ---------------------------------------------------------------------------

export async function listBackendEvents(
  limit = 50,
): Promise<{ total: number; events: BackendNormalizedEvent[] }> {
  return backendFetch<{ total: number; events: BackendNormalizedEvent[] }>(
    `/events?limit=${limit}`,
  );
}

export async function getBackendSourceEvents(
  sourceId: string,
  limit = 50,
): Promise<{ total: number; events: BackendNormalizedEvent[] }> {
  return backendFetch<{ total: number; events: BackendNormalizedEvent[] }>(
    `/sources/${encodeURIComponent(sourceId)}/events?limit=${limit}`,
  );
}

export interface BackendEventStreamHandlers {
  onEvent: (event: BackendNormalizedEvent) => void;
  onOpen?: () => void;
  onError?: () => void;
  onReset?: () => void;
}

export function subscribeToBackendEvents(
  sourceId: string | undefined,
  handlers: BackendEventStreamHandlers,
): () => void {
  if (typeof window === "undefined" || typeof EventSource === "undefined")
    return () => {};

  const query = sourceId ? `?source_id=${encodeURIComponent(sourceId)}` : "";
  const stream = new EventSource(`/backend/events/stream${query}`);
  const handleMessage = (event: Event) => {
    try {
      const payload = JSON.parse(
        (event as MessageEvent<string>).data,
      ) as BackendNormalizedEvent;
      if (
        payload &&
        typeof payload === "object" &&
        typeof payload.event_id === "string"
      ) {
        handlers.onEvent(payload);
      }
    } catch {
      handlers.onError?.();
    }
  };

  const handleReset = () => handlers.onReset?.();
  stream.addEventListener("normalized", handleMessage);
  stream.addEventListener("reset", handleReset);
  stream.onopen = () => handlers.onOpen?.();
  stream.onerror = () => handlers.onError?.();

  return () => {
    stream.removeEventListener("normalized", handleMessage);
    stream.removeEventListener("reset", handleReset);
    stream.close();
  };
}

export function mergeBackendEvents(
  current: BackendNormalizedEvent[],
  incoming: BackendNormalizedEvent[],
  limit = 50,
): BackendNormalizedEvent[] {
  const byId = new Map<string, BackendNormalizedEvent>();
  for (const event of [...current, ...incoming]) {
    if (event.event_id) byId.set(event.event_id, event);
  }
  return [...byId.values()]
    .sort((left, right) => backendEventTime(right) - backendEventTime(left))
    .slice(0, limit);
}

function backendEventTime(event: BackendNormalizedEvent): number {
  return typeof event.time === "number"
    ? event.time
    : Date.parse(event.time) || 0;
}

export async function listBackendDlq(
  limit = 200,
): Promise<{ total: number; records: BackendDlqRecord[] }> {
  return backendFetch<{ total: number; records: BackendDlqRecord[] }>(
    `/dlq?limit=${limit}`,
  );
}

// ---------------------------------------------------------------------------
// Module 3: reprocessing
//
// The browser never parses. It asks the API to republish the original Bronze
// event onto logs.raw, then polls for the orchestrator's verdict. Every
// "recovered" claim below therefore comes from the pipeline, not from a
// re-run of the local normalizer.
// ---------------------------------------------------------------------------

export type ReprocessStatus =
  | "queued"
  | "running"
  | "completed"
  | "partial"
  | "failed";

export interface ReprocessRunError {
  dlq_id: string;
  detail: string;
}

/** Mirrors ReprocessRun / serialize_run() on the Python side. */
export interface ReprocessRun {
  reprocess_id: string;
  status: ReprocessStatus;
  requested_count: number;
  published_count: number;
  recovered_count: number;
  failed_count: number;
  reason?: string | null;
  dlq_ids: string[];
  event_ids: string[];
  errors: ReprocessRunError[];
  created_at?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  /** Only on the response of POST /dlq/{id}/reprocess. */
  dlq_id?: string;
}

export interface DryRunPreview {
  dlq_id: string;
  dry_run: true;
  would_succeed: boolean;
  /**
   * True when the verdict depends on the orchestrator's live Drain3 miner,
   * which this process cannot see. A real replay may still recover it, so a
   * false here is NOT a death sentence.
   */
  drain3_dependent?: boolean;
  note?: string | null;
  parsers_attempted?: string[];
  previous_parsers_attempted?: string[];
  normalized_preview?: unknown;
  error?: string;
}

export interface BatchDryRunResult {
  dry_run: true;
  requested: number;
  would_succeed: number;
  drain3_dependent: number;
  results: DryRunPreview[];
}

const TERMINAL_STATUSES: ReadonlySet<ReprocessStatus> = new Set([
  "completed",
  "partial",
  "failed",
]);

export function isReprocessTerminal(status: ReprocessStatus): boolean {
  return TERMINAL_STATUSES.has(status);
}

/**
 * Ids are always explicit. The API deliberately has no "reprocess everything
 * matching a filter" mode, and the UI must not reintroduce one: a bulk replay
 * that silently picks its own targets is unreviewable.
 */
export async function reprocessDlqBatch(
  dlqIds: string[],
  reason?: string,
): Promise<ReprocessRun> {
  return backendFetch<ReprocessRun>(
    "/dlq/reprocess",
    jsonInit("POST", { dlq_ids: dlqIds, reason: reason ?? null }),
  );
}

export async function reprocessDlqRecord(
  dlqId: string,
  reason?: string,
): Promise<ReprocessRun> {
  return backendFetch<ReprocessRun>(
    `/dlq/${encodeURIComponent(dlqId)}/reprocess`,
    jsonInit("POST", { reason: reason ?? null }),
  );
}

/**
 * Preview a replay without publishing anything, so a batch decision can be
 * made on evidence rather than hope.
 */
export async function previewReprocessBatch(
  dlqIds: string[],
): Promise<BatchDryRunResult> {
  return backendFetch<BatchDryRunResult>(
    "/dlq/reprocess?dry_run=true",
    jsonInit("POST", { dlq_ids: dlqIds, reason: null, dry_run: true }),
  );
}

export async function getReprocessRun(
  reprocessId: string,
): Promise<ReprocessRun> {
  return backendFetch<ReprocessRun>(
    `/reprocess/${encodeURIComponent(reprocessId)}`,
  );
}

function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new DOMException("Aborted", "AbortError"));
      return;
    }
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    function onAbort() {
      clearTimeout(timer);
      reject(new DOMException("Aborted", "AbortError"));
    }
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

export interface PollReprocessOptions {
  signal?: AbortSignal;
  intervalMs?: number;
  timeoutMs?: number;
  onUpdate?: (run: ReprocessRun) => void;
}

/**
 * Follow a run to a terminal status.
 *
 * Returns the last observed run on timeout rather than throwing, so a stuck
 * run still shows the operator its real counters instead of an error. Only
 * "completed", "partial" and "failed" are terminal -- a run stays "running"
 * until the orchestrator has tallied an outcome for every published event, so
 * polling must not stop early or it will report 0 recovered for a run that is
 * about to succeed.
 */
export async function pollReprocessRun(
  reprocessId: string,
  opts: PollReprocessOptions = {},
): Promise<ReprocessRun> {
  const { signal, intervalMs = 1500, timeoutMs = 120_000, onUpdate } = opts;
  const deadline = Date.now() + timeoutMs;
  let run = await getReprocessRun(reprocessId);
  onUpdate?.(run);
  while (!isReprocessTerminal(run.status)) {
    if (Date.now() >= deadline) return run;
    await sleep(intervalMs, signal);
    run = await getReprocessRun(reprocessId);
    onUpdate?.(run);
  }
  return run;
}

export async function getBackendDashboard(
  limit = 5,
): Promise<BackendDashboard> {
  return backendFetch<BackendDashboard>(`/dashboard?limit=${limit}`);
}

// ---------------------------------------------------------------------------
// Drain3 clustering (backed by orchestrator/parsers/drain_miner.py)
// ---------------------------------------------------------------------------

export interface DrainCluster {
  template: string;
  count: number;
  examples: string[];
}

export async function clusterDrainLogs(
  content: string,
): Promise<DrainCluster[]> {
  const data = await backendFetch<{ clusters: DrainCluster[] }>(
    "/drain/cluster",
    jsonInit("POST", { content }),
  );
  return data.clusters ?? [];
}

// ---------------------------------------------------------------------------
// Backend → UI source adapter (keeps the SourceRegistry UI shape)
// ---------------------------------------------------------------------------

const TYPE_TO_UI: Record<string, string> = {
  network_device: "firewall",
  server: "edr",
  application: "application",
  database: "database",
  cloud: "cloud",
  iot: "iot",
  custom: "custom",
};

const TRANSPORT_TO_UI: Record<string, SourceTransportId> = {
  udp: "syslog_udp",
  http: "http_collect",
  file: "file_agent",
  sse: "sse_stream",
  other: "custom_api",
  kafka_sim: "kafka_sim",
};

const TRANSPORT_ENDPOINT: Record<string, string> = {
  udp: "1514/udp",
  http: "http://localhost:8081/logs",
  file: "/var/lib/ulpf/uploads",
  sse: "https://<upstream-host>/events",
  other: "http://localhost:8081/logs",
};

const DEFAULT_CHAIN: Record<string, string[]> = {
  syslog_udp: ["Syslog Parser", "Key/Value Parser", "Text Parser"],
  syslog_tcp: ["Syslog Parser", "Key/Value Parser", "Text Parser"],
  http_collect: ["JSON Parser", "Key/Value Parser", "Text Parser"],
  sse_stream: ["JSON Parser", "Key/Value Parser", "Text Parser"],
  file_agent: ["Format auto-detect", "Primary chain · set on first import"],
  custom_api: ["JSON Parser", "Key/Value Parser", "Text Parser"],
};

export function uiTypeForBackend(sourceType: string): string {
  return TYPE_TO_UI[sourceType] ?? "custom";
}

export function uiTransportForBackend(transport: string): SourceTransportId {
  return TRANSPORT_TO_UI[transport] ?? "custom_api";
}

export function endpointForTransport(transport: string): string {
  return TRANSPORT_ENDPOINT[transport] ?? "/api/normalize";
}

export function backendSourceToUi(doc: BackendSourceDoc): Source {
  const transport = uiTransportForBackend(doc.transport);
  return {
    id: doc.source_id,
    name: doc.name,
    typeId: uiTypeForBackend(doc.source_type),
    transport,
    endpoint: endpointForTransport(doc.transport),
    format: doc.expected_format,
    parser_chain: DEFAULT_CHAIN[transport] ?? DEFAULT_CHAIN.http_collect,
    createdAt: Date.parse(doc.created_at) || Date.now(),
  };
}

// ---------------------------------------------------------------------------
// Backend → UI event adapters
// ---------------------------------------------------------------------------

export const SEVERITY_IDS: Record<string, number> = {
  low: 2,
  medium: 3,
  high: 4,
  critical: 5,
};

const CLASS_TABLE: Record<string, { class_uid: number; category_uid: number }> =
  {
    system_activity: { class_uid: 1007, category_uid: 1 },
    security_activity: { class_uid: 2002, category_uid: 2 },
    application_activity: { class_uid: 6001, category_uid: 6 },
  };

const SEVERITY_DISPLAY: Record<string, string> = {
  low: "low",
  medium: "medium",
  high: "high",
  critical: "critical",
};

export function backendEventToOcsf(be: BackendNormalizedEvent): OcsfEvent {
  const cls = CLASS_TABLE[be.class_name ?? ""] ?? {
    class_uid: 0,
    category_uid: 0,
  };
  const severityRaw = (be.severity ?? "").toLowerCase();
  const severityId = SEVERITY_IDS[severityRaw] ?? 0;
  const time = typeof be.time === "string" ? Date.parse(be.time) : be.time;

  const src = be.src_endpoint ? { ip: be.src_endpoint } : undefined;
  const dst = be.dst_endpoint ? { ip: be.dst_endpoint } : undefined;

  return {
    time,
    class_uid: cls.class_uid,
    class_name: be.class_name ?? undefined,
    category_uid: cls.category_uid,
    category_name: be.category_name ?? undefined,
    activity_id: 0,
    activity_name: be.activity_name ?? undefined,
    type_uid: cls.class_uid * 100,
    type_name: be.activity_name ?? undefined,
    severity_id: severityId,
    severity: severityRaw ? SEVERITY_DISPLAY[severityRaw] : undefined,
    status_id: undefined,
    message: be.app_id
      ? `${be.app_id}${be.user ? ` · ${be.user}` : ""}${be.action ? ` · ${be.action}` : ""}`
      : undefined,
    src_endpoint: src,
    dst_endpoint: dst,
    user: be.user ? { name: be.user } : undefined,
    device: be.device_id ? { hostname: be.device_id } : undefined,
    app_name: be.app_id ?? undefined,
    connection_info: be.protocol_name
      ? { protocol_name: be.protocol_name }
      : undefined,
    raw_data: undefined,
    metadata: { version: "1.3.0" },
    extensions: {
      ...(be.extensions ?? {}),
      event_id: be.event_id,
      raw_event_id: be.raw_event_id,
      parser_id: be.parser_id,
      parser_tier: be.parser_tier,
      confidence_score: be.confidence_score,
    },
  };
}

export function backendEventToLineResult(
  be: BackendNormalizedEvent,
  index: number,
): NormalizedEventResult {
  return {
    ok: true,
    event: backendEventToOcsf(be),
    source_line: "",
    line_number: index + 1,
    format: (be.extensions?.format_hint as string | undefined) ?? "unknown",
    parser: be.parser_id,
    parser_chain: [be.parser_tier],
    chain_rescued: be.parser_tier === "fallback",
    parsed: [be.extensions ?? {}],
  };
}

export function backendEventSourceId(
  event: BackendNormalizedEvent,
): string | undefined {
  const value = event.extensions?.source_id;
  return value === undefined || value === null ? undefined : String(value);
}

export function backendDlqToRejected(
  rec: BackendDlqRecord,
  index: number,
): RejectedEventResult {
  return {
    ok: false,
    reason: rec.classification ?? rec.status,
    line: rec.raw_payload ?? null,
    line_number: index + 1,
    tried_parsers: rec.parsers_attempted ?? [],
    // Carries the real identity through the shared display shape so a
    // reprocess request addresses the record, not its row index.
    dlq_id: rec.dlq_id,
  };
}
