export const API_BASE =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export interface Source {
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

export interface UploadJob {
  upload_id: string;
  source_id: string;
  filename: string;
  status: string;
  created_at: string;
  started_at?: string | null;
  completed_at?: string | null;
  total_lines: number;
  published_events: number;
  blank_lines: number;
  line_errors: number;
  normalized_events?: number;
  dlq_events?: number;
  raw_events?: number;
  failure_reason?: string | null;
}

export interface NormalizedEvent {
  event_id: string;
  raw_event_id: string;
  parser_id: string;
  parser_tier: string;
  confidence_score: number;
  schema_id?: string;
  schema_version?: string;
  class_name?: string | null;
  category_name?: string | null;
  activity_name?: string | null;
  severity?: string | null;
  time: string;
  user?: string | null;
  device_id?: string | null;
  app_id?: string | null;
  src_endpoint?: string | null;
  dst_endpoint?: string | null;
  protocol_name?: string | null;
  action?: string | null;
  extensions?: Record<string, unknown>;
}

export interface RawEvent {
  event_id: string;
  ingested_at: string;
  source_id: string;
  source_type: string;
  transport: string;
  format_hint?: string | null;
  raw_payload: string;
  bronze_uri?: string | null;
  collector_id: string;
  envelope_schema_version?: string;
  upload_id?: string | null;
}

export interface DlqRecord {
  dlq_id: string;
  raw_event_id: string;
  raw_payload?: string | null;
  parsers_attempted: string[];
  status: string;
  classification?: string | null;
  first_seen_at: string;
  last_attempt_at: string;
  reprocess_count?: number;
  metadata?: Record<string, unknown>;
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

async function parseError(res: Response): Promise<Error> {
  let detail = res.statusText;
  try {
    const body = await res.json();
    detail = body.detail ?? JSON.stringify(body);
  } catch {
    /* keep statusText */
  }
  return new Error(`${res.status}: ${detail}`);
}

export async function getJSON<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { cache: "no-store" });
  if (!res.ok) throw await parseError(res);
  return res.json();
}

export async function postJSON<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw await parseError(res);
  return res.json();
}

export async function uploadFile<T>(
  path: string,
  file: File
): Promise<T> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    body: form,
  });
  if (!res.ok) throw await parseError(res);
  return res.json();
}

export function fmtTime(iso?: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString();
}

export function fmtNumber(n: number | undefined | null): string {
  if (n === undefined || n === null) return "—";
  return n.toLocaleString();
}

export const SOURCE_TYPES = [
  { value: "server", label: "Server" },
  { value: "network_device", label: "Network Device" },
  { value: "application", label: "Application" },
  { value: "database", label: "Database" },
  { value: "cloud", label: "Cloud Service" },
  { value: "iot", label: "IoT Device" },
  { value: "custom", label: "Custom" },
];

export const EXPECTED_FORMATS = [
  { value: "mixed", label: "Mixed" },
  { value: "auto", label: "Auto" },
  { value: "syslog", label: "Syslog" },
  { value: "json", label: "JSON" },
  { value: "cef", label: "CEF" },
];

export const TRANSPORTS = [
  { value: "file", label: "File" },
  { value: "udp", label: "UDP" },
  { value: "http", label: "HTTP" },
  { value: "other", label: "Other" },
];