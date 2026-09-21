/**
 * Shared code between client and server
 * Useful to share types between client and server
 * and/or small pure JS functions that can be used on both client and server
 */

export interface DemoResponse {
  message: string;
}

//
// OCSF Normalizer — shared API contract
//

export type LogFormat = "json" | "syslog" | "cef" | "leef" | "keyvalue" | "apache" | "text";

export interface OcsfEndpoint {
  ip?: string;
  port?: number;
  hostname?: string;
}

export interface OcsfEvent {
  activity_id: number;
  activity_name?: string;
  category_uid: number;
  category_name?: string;
  class_uid: number;
  class_name?: string;
  severity_id: number;
  severity?: string;
  status_id?: number;
  status?: string;
  action_id?: number;
  action?: string;
  disposition_id?: number;
  disposition?: string;
  type_uid: number;
  type_name?: string;
  time: number;
  time_dt?: string;
  timezone_offset?: number;
  message?: string;
  app_name?: string;
  metadata: {
    version: string;
    labels?: string[];
    log?: {
      name?: string;
      provider?: string;
      product?: string;
      version?: string;
    };
  };
  src_endpoint?: OcsfEndpoint;
  dst_endpoint?: OcsfEndpoint;
  device?: { hostname?: string; uid?: string };
  user?: { uid?: string; name?: string };
  actor?: { user?: { uid?: string; name?: string } };
  process?: { name?: string; pid?: number; cmd_line?: string; path?: string };
  http_request?: {
    method?: string;
    user_agent?: string;
    url?: { path?: string; hostname?: string; url_string?: string };
  };
  http_response?: { code?: number };
  api?: { operation?: string; service?: { name?: string } };
  query?: { hostname?: string; type?: string };
  answers?: { ip?: string }[];
  rcode_id?: number;
  rcode?: string;
  connection_info?: { protocol_name?: string; protocol_num?: number; direction?: string };
  firewall_rule?: { name?: string; uid?: string };
  database?: { name?: string; type?: string };
  table?: { name?: string };
  query_info?: { query_string?: string };
  raw_data?: string;
  unmapped?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface NormalizedEventResult {
  ok: true;
  event: OcsfEvent;
  source_line: string;
  line_number: number;
  format: string;
  parser: string;
  parser_chain: string[];
  chain_rescued: boolean;
  parsed: Record<string, unknown>[];
}

export interface RejectedEventResult {
  ok: false;
  reason: string;
  line: string | null;
  line_number: number;
  tried_parsers?: string[];
}

export type LineResult = NormalizedEventResult | RejectedEventResult;

export interface NormalizationSummary {
  total_lines: number;
  events: number;
  rejected: number;
  skipped: number;
  rescued: number;
  source_format: LogFormat | "mixed" | "auto";
  by_class: Record<string, number>;
  by_format: Record<string, number>;
}

export interface NormalizeRequest {
  content: string;
  source?: {
    name?: string;
    format?: LogFormat | "auto";
  };
}

export interface NormalizeResponse {
  ok: boolean;
  error?: string;
  lines: LineResult[];
  summary: NormalizationSummary;
}