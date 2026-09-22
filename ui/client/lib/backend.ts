import type { NormalizedEventResult, OcsfEvent, RejectedEventResult } from "@shared/api";
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

export async function createBackendSource(input: BackendSourceCreate): Promise<BackendSourceDoc> {
  const data = await backendFetch<{ source: BackendSourceDoc }>("/sources", jsonInit("POST", input));
  return data.source;
}

export async function updateBackendSource(
  sourceId: string,
  patch: Partial<Pick<BackendSourceDoc, "name" | "expected_format" | "enabled" | "description">>,
): Promise<BackendSourceDoc> {
  const data = await backendFetch<{ source: BackendSourceDoc }>(`/sources/${sourceId}`, jsonInit("PUT", patch));
  return data.source;
}

export async function deleteBackendSource(sourceId: string): Promise<void> {
  await backendFetch<unknown>(`/sources/${sourceId}`, { method: "DELETE" });
}

export async function getBackendSourceStats(sourceId: string): Promise<SourceStats> {
  return backendFetch<SourceStats>(`/sources/${sourceId}/stats`);
}

// ---------------------------------------------------------------------------
// Events & DLQ
// ---------------------------------------------------------------------------

export async function listBackendEvents(limit = 50): Promise<{ total: number; events: BackendNormalizedEvent[] }> {
  return backendFetch<{ total: number; events: BackendNormalizedEvent[] }>(`/events?limit=${limit}`);
}

export async function getBackendSourceEvents(sourceId: string, limit = 50): Promise<{ total: number; events: BackendNormalizedEvent[] }> {
  return backendFetch<{ total: number; events: BackendNormalizedEvent[] }>(`/sources/${sourceId}/events?limit=${limit}`);
}

export async function listBackendDlq(limit = 200): Promise<{ total: number; records: BackendDlqRecord[] }> {
  return backendFetch<{ total: number; records: BackendDlqRecord[] }>(`/dlq?limit=${limit}`);
}

export async function getBackendDashboard(limit = 5): Promise<BackendDashboard> {
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

export async function clusterDrainLogs(content: string): Promise<DrainCluster[]> {
  const data = await backendFetch<{ clusters: DrainCluster[] }>("/drain/cluster", jsonInit("POST", { content }));
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
  other: "custom_api",
};

const TRANSPORT_ENDPOINT: Record<string, string> = {
  udp: "514",
  http: "/api/normalize",
  file: "/var/lib/ulpf/uploads",
  other: "/api/normalize",
};

const DEFAULT_CHAIN: Record<string, string[]> = {
  syslog_udp: ["Syslog Parser", "Key/Value Parser", "Text Parser"],
  syslog_tcp: ["Syslog Parser", "Key/Value Parser", "Text Parser"],
  http_collect: ["JSON Parser", "Key/Value Parser", "Text Parser"],
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

export const SEVERITY_IDS: Record<string, number> = { low: 2, medium: 3, high: 4, critical: 5 };

const CLASS_TABLE: Record<string, { class_uid: number; category_uid: number }> = {
  system_activity: { class_uid: 1007, category_uid: 1 },
  security_activity: { class_uid: 2002, category_uid: 2 },
  application_activity: { class_uid: 6001, category_uid: 6 },
};

const SEVERITY_DISPLAY: Record<string, string> = { low: "low", medium: "medium", high: "high", critical: "critical" };

export function backendEventToOcsf(be: BackendNormalizedEvent): OcsfEvent {
  const cls = CLASS_TABLE[be.class_name ?? ""] ?? { class_uid: 0, category_uid: 0 };
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
    message: be.app_id ? `${be.app_id}${be.user ? ` · ${be.user}` : ""}${be.action ? ` · ${be.action}` : ""}` : undefined,
    src_endpoint: src,
    dst_endpoint: dst,
    user: be.user ? { name: be.user } : undefined,
    device: be.device_id ? { hostname: be.device_id } : undefined,
    app_name: be.app_id ?? undefined,
    connection_info: be.protocol_name ? { protocol_name: be.protocol_name } : undefined,
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

export function backendEventToLineResult(be: BackendNormalizedEvent, index: number): NormalizedEventResult {
  return {
    ok: true,
    event: backendEventToOcsf(be),
    source_line: "",
    line_number: index + 1,
    format: be.extensions?.format_hint as string | undefined ?? "unknown",
    parser: be.parser_id,
    parser_chain: [be.parser_tier],
    chain_rescued: be.parser_tier === "fallback",
    parsed: [be.extensions ?? {}],
  };
}

export function backendDlqToRejected(rec: BackendDlqRecord, index: number): RejectedEventResult {
  return {
    ok: false,
    reason: rec.classification ?? rec.status,
    line: rec.raw_payload ?? null,
    line_number: index + 1,
    tried_parsers: rec.parsers_attempted ?? [],
  };
}