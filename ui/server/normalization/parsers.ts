import { LogFormat } from "../../shared/api";
import { actionFromText, dispositionFromText, severityFromCef, severityFromLevel, severityFromSyslog, statusFromText } from "./ocsf";
import { parseTimestamp, ParsedTime } from "./time";

/**
 * Intermediate representation produced by a format parser and consumed by the
 * OCSF classifier. Every field is a real value pulled from the source line;
 * nothing is defaulted except explicit fallbacks documented in the code.
 */
export interface ParsedEvent {
  raw: string;
  format: LogFormat;
  parser: string;
  timestamp?: number;
  tzOffset?: number;
  message?: string;
  level?: string;
  severityId?: number;
  statusId?: number;
  actionId?: number;
  dispositionId?: number;
  srcIp?: string;
  srcPort?: number;
  srcHost?: string;
  dstIp?: string;
  dstPort?: number;
  dstHost?: string;
  user?: string;
  userUid?: string;
  hostname?: string;
  appName?: string;
  operation?: string;
  httpMethod?: string;
  httpStatus?: number;
  url?: string;
  path?: string;
  userAgent?: string;
  referrer?: string;
  protocol?: string;
  processName?: string;
  processId?: number;
  commandLine?: string;
  parentProcess?: string;
  dnsQuery?: string;
  dnsType?: string;
  dnsAnswers?: string[];
  dnsRcode?: string;
  threat?: string;
  ruleName?: string;
  eventType?: string;
  eventName?: string;
  eventCategory?: string;
  accountName?: string;
  authProtocol?: string;
  logonType?: string;
  vendor?: string;
  product?: string;
  vendorVersion?: string;
  dbName?: string;
  tableName?: string;
  queryString?: string;
  statusText?: string;
  actionText?: string;
  unmapped: Record<string, unknown>;
  parsed_objects: number;
}

const NOISE_ONLY = new Set(["-", "--", "n/a", "na", "none", "null", "nil", "undefined", "unknown", "?", "…"]);

export function cleanString(v: unknown): string | undefined {
  if (v === undefined || v === null) return undefined;
  if (typeof v === "number") return Number.isFinite(v) ? String(v) : undefined;
  if (typeof v === "boolean") return String(v);
  if (typeof v !== "string") return undefined;
  const s = v.replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/g, "").replace(/\s+/g, " ").trim();
  if (s === "") return undefined;
  const lower = s.toLowerCase();
  if (NOISE_ONLY.has(lower)) return undefined;
  return s;
}

export function cleanInt(v: unknown): number | undefined {
  const s = cleanString(v);
  if (!s) return undefined;
  const compact = s.replace(/,/g, "");
  if (/^[+-]?\d+$/.test(compact)) {
    const n = Number(compact);
    if (Number.isSafeInteger(n)) return n;
  }
  return undefined;
}

export function cleanPort(v: unknown): number | undefined {
  const n = cleanInt(v);
  if (n === undefined) return undefined;
  if (n < 1 || n > 65535) return undefined;
  return n;
}

const IPV4_RE = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/;
function isIp(v: string): boolean {
  if (IPV4_RE.test(v)) {
    return IPV4_RE.exec(v)!.slice(1).every((o) => Number(o) <= 255);
  }
  return /^[0-9a-fA-F:]{2,45}$/.test(v) && v.includes(":");
}

export function cleanIp(v: unknown): string | undefined {
  const s = cleanString(v);
  if (!s) return undefined;
  if (s === "0.0.0.0" || s === "::" || s === "::1" || s === "127.0.0.1") return s;
  if (!isIp(s)) return undefined;
  return s;
}

// ---------------------------------------------------------------------------
// JSON
// ---------------------------------------------------------------------------

function flattenJson(value: unknown, out: Record<string, unknown> = {}, prefix = "", depth = 0): Record<string, unknown> {
  if (value === null || typeof value !== "object") {
    out[prefix] = value;
    return out;
  }
  if (Array.isArray(value)) {
    if (value.every((i) => i === null || typeof i !== "object")) {
      out[prefix] = value;
      return out;
    }
    if (depth < 4) {
      value.forEach((item, idx) => flattenJson(item, out, prefix ? `${prefix}[${idx}]` : `[${idx}]`, depth + 1));
    }
    return out;
  }
  if (depth < 6) {
    for (const [key, val] of Object.entries(value)) {
      const joined = prefix ? `${prefix}.${key}` : key;
      flattenJson(val, out, joined, depth + 1);
    }
  } else {
    out[prefix] = value;
  }
  return out;
}

// ---------------------------------------------------------------------------
// Flat key extraction (shared by JSON, key=value, CEF and LEEF)
// ---------------------------------------------------------------------------

function firstOf(map: Record<string, unknown>, keys: string[]): unknown {
  for (const k of keys) {
    if (Object.prototype.hasOwnProperty.call(map, k)) {
      const v = map[k];
      if (v !== undefined && v !== null && v !== "") return v;
    }
  }
  return undefined;
}

function stringOf(map: Record<string, unknown>, keys: string[]): string | undefined {
  return cleanString(firstOf(map, keys));
}

const TIME_KEYS = ["@timestamp", "timestamp", "ts", "time", "datetime", "date", "eventtime", "event_time", "event.time", "occurred", "@time", "created", "created_at", "createtime", "logtime", "utc_time", "utctime", "receivedtime", "received", "begintime", "start_time", "devtime"];
const EXTRA_TIME_KEYS_CEF = ["rt"];
const MESSAGE_KEYS = ["message", "msg", "log.message", "event.message", "message_text", "msg_text", "description", "details", "event_description"];
const LEVEL_KEYS = ["level", "log_level", "loglevel", "severity", "event.severity", "log.level", "priority", "priority_name", "lvl", "severity_label"];
const STATUS_KEYS = ["result", "outcome", "event.outcome", "event_result", "login_result", "auth_result", "auth_result_reason", "login_success"];
const ACTION_KEYS = ["action", "act", "event.action", "event_action", "decision", "verdict", "action_result"];
const SRC_IP_KEYS = ["src", "src_ip", "source_ip", "sourceip", "srcip", "sip", "source_address", "saddr", "ip_src", "srcaddr", "client_ip", "clientip", "c_ip", "remote_ip", "remoteip", "source.ip", "src.ip", "src_endpoint.ip", "source_endpoint.ip", "client.ip", "src_ip_addr"];
const DST_IP_KEYS = ["dst", "dst_ip", "dest_ip", "destination_ip", "destip", "dstip", "dip", "destination_address", "daddr", "ip_dst", "dstaddr", "server_ip", "serverip", "s_ip", "destination.ip", "dst.ip", "dst_endpoint.ip", "destination_endpoint.ip", "server.ip", "destinationip"];
const SRC_PORT_KEYS = ["sport", "spt", "src_port", "source_port", "sourceport", "srcport", "src_endpoint.port", "source.port", "client_port", "source_port_number"];
const DST_PORT_KEYS = ["dport", "dpt", "dst_port", "dest_port", "destination_port", "dstport", "dst_endpoint.port", "destination.port", "server_port", "destination_port_number"];
const SRC_HOST_KEYS = ["shost", "source_hostname", "src_host", "src_hostname", "src_endpoint.hostname", "src.hostname", "client_hostname", "client_host", "remote_hostname", "source_host"];
const DST_HOST_KEYS = ["dhost", "destination_hostname", "dst_host", "dst_hostname", "dst_endpoint.hostname", "dst.hostname", "server_hostname", "server_host", "destination_host"];
const HOSTNAME_KEYS = ["hostname", "host_name", "host.hostname", "device_hostname", "computer", "computername", "machine_name", "agent.name", "agent.hostname", "dvchost", "dhost.device"];
const USER_KEYS = ["user", "username", "user_name", "principal", "account_name", "subject_user", "user.name", "user.username", "user.principal.name", "src_user", "remote_user", "userName", "login", "login_name", "subject", "suser", "sourceuser"];
const USER_UID_KEYS = ["user.uid", "user.id", "user_id", "subject_userid", "user_name_id", "user.account_uid"];
const APP_KEYS = ["app", "app_name", "application", "application_name", "service", "service_name", "service.name", "appname", "feature_name", "processname_src"];
const OPERATION_KEYS = ["operation", "operation_name", "event.operation", "api.operation", "aws.operation", "activity", "activityname", "user_action"];
const HTTP_METHOD_KEYS = ["method", "http_method", "request_method", "http.request.method", "req_method", "httprequestmethod"];
const HTTP_STATUS_KEYS = ["http_status", "http.response.status", "httpresponse_status", "response.status", "response_code", "sc", "http_status_code", "response.code", "http.code"];
const URL_KEYS = ["url", "request_url", "uri", "http_request.url", "url.original", "full_url", "request_uri", "http.url", "url_original"];
const PATH_KEYS = ["path", "uri_path", "request_path", "url.path", "http_request.path", "route", "endpoint", "resource_path"];
const UA_KEYS = ["user_agent", "ua", "useragent", "http_request.user_agent", "request_client_application", "http.user_agent", "agent", "client_user_agent"];
const REFERRER_KEYS = ["referer", "referrer", "http_referrer", "http.referer", "referrer_url"];
const PROTOCOL_KEYS = ["protocol", "proto", "transport", "transport_protocol", "connection_info.protocol_name", "protocol_name", "proto_name", "network.protocol", "proto_ver"];
const PROCESS_NAME_KEYS = ["process_name", "processname", "proc", "process.name", "exe", "image_name", "image", "executable_name", "program_name", "process.command"];
const PROCESS_ID_KEYS = ["pid", "process_id", "process.pid", "processid", "process_id_num"];
const CMD_LINE_KEYS = ["cmd", "command_line", "cmd_line", "process.cmd_line", "process.command_line", "command", "args_string", "arguments", "cmdline", "process.args"];
const PARENT_PROCESS_KEYS = ["parent_process", "ppid", "parent_process_id", "parent_process_name", "parent.name"];
const DNS_QUERY_KEYS = ["dns_query", "qname", "query_name", "dns.qname", "dns.query", "query.hostname", "dns_query_name", "dns.request.qname"];
const DNS_TYPE_KEYS = ["dns_type", "query_type", "qtype", "dns.query_type", "dns.type", "record_type", "dns.response.type"];
const DNS_ANSWERS_KEYS = ["dns.answers", "answers", "answer", "dns.response.answer", "dns_answer", "dns.response.answers"];
const DNS_RCODE_KEYS = ["dns.rcode", "rcode", "dns.response.code", "dns.responsecode", "dns.returncode"];
const THREAT_KEYS = ["threat_name", "threat.name", "malware.name", "detection_name", "alert_name", "threat", "signature_name", "attack.name", "event.threat.name"];
const RULE_KEYS = ["rule_name", "rulename", "rule", "firewall_rule", "firewall_rule.name", "signature", "policy_name", "policy"];
const EVENT_TYPE_KEYS = ["event_type", "eventtype", "event.type", "event_category", "eventcategory", "category", "category.name", "event_code", "eventid", "event.id", "event_id", "signature_id", "eventcode", "log_type", "winlog.event_id"];
const EVENT_NAME_KEYS = ["event_name", "event.name", "name", "log_name", "eventlogname", "provider_name", "rule.name", "event_type_name"];
const ACCOUNT_KEYS = ["account", "accountname", "user.account.name", "account_name_domain", "target_user", "target_account", "responsible_user", "user.account.uid"];
const AUTH_PROTOCOL_KEYS = ["auth_protocol", "authprotocol", "auth.protocol", "authentication_protocol", "protocol_authentication"];
const LOGON_TYPE_KEYS = ["logon_type", "logon_type_id", "login_type", "logintype", "logon_type_name"];
const DB_KEYS = ["database.name", "db_name", "database", "db", "database_name", "dbname", "datastore.name"];
const TABLE_KEYS = ["table.name", "table_name", "table", "tablename"];
const QUERY_STRING_KEYS = ["query_string", "query.text", "sql", "sqltext", "statement", "query"];
const VENDOR_KEYS = ["vendor", "device_vendor", "dvendor", "vendor_name", "devicevendor"];
const PRODUCT_KEYS = ["device_product", "deviceproduct", "product_name", "dproduct", "product"];
const VERSION_KEYS = ["device_version", "dvendor_version", "version", "dvversion", "product_version"];

function httpStatusOf(map: Record<string, unknown>): number | undefined {
  const v = stringOf(map, HTTP_STATUS_KEYS);
  if (v === undefined) {
    const generic = firstOf(map, ["status"]);
    const n = cleanInt(generic);
    if (n !== undefined && n >= 100 && n <= 599) return n;
    return undefined;
  }
  const n = cleanInt(v);
  if (n !== undefined && n >= 100 && n <= 599) return n;
  return undefined;
}

function dnsAnswersOf(map: Record<string, unknown>): string[] | undefined {
  const v = firstOf(map, DNS_ANSWERS_KEYS);
  if (v === undefined) return undefined;
  const vals = Array.isArray(v) ? v : [v];
  const out: string[] = [];
  for (const item of vals) {
    if (item === null || item === undefined) continue;
    if (typeof item === "string") {
      const c = cleanIp(item);
      if (c) out.push(c);
    } else if (typeof item === "object") {
      const ip = (item as Record<string, unknown>).ip ?? (item as Record<string, unknown>).address;
      const c = cleanIp(ip);
      if (c) out.push(c);
    }
  }
  return out.length > 0 ? out : undefined;
}

const KNOWN_KEYS = new Set<string>(
  [TIME_KEYS, EXTRA_TIME_KEYS_CEF, MESSAGE_KEYS, LEVEL_KEYS, STATUS_KEYS, ACTION_KEYS, SRC_IP_KEYS, DST_IP_KEYS, SRC_PORT_KEYS, DST_PORT_KEYS, SRC_HOST_KEYS, DST_HOST_KEYS, HOSTNAME_KEYS, USER_KEYS, USER_UID_KEYS, APP_KEYS, OPERATION_KEYS, HTTP_METHOD_KEYS, HTTP_STATUS_KEYS, URL_KEYS, PATH_KEYS, UA_KEYS, REFERRER_KEYS, PROTOCOL_KEYS, PROCESS_NAME_KEYS, PROCESS_ID_KEYS, CMD_LINE_KEYS, PARENT_PROCESS_KEYS, DNS_QUERY_KEYS, DNS_TYPE_KEYS, DNS_ANSWERS_KEYS, DNS_RCODE_KEYS, THREAT_KEYS, RULE_KEYS, EVENT_TYPE_KEYS, EVENT_NAME_KEYS, ACCOUNT_KEYS, AUTH_PROTOCOL_KEYS, LOGON_TYPE_KEYS, DB_KEYS, TABLE_KEYS, QUERY_STRING_KEYS, VENDOR_KEYS, PRODUCT_KEYS, VERSION_KEYS, ["status", "bytes", "hit", "reason", "src_mac", "dst_mac", "device_facility", "facility"]].flat(),
);

function unmappedOf(map: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(map)) {
    if (KNOWN_KEYS.has(k)) continue;
    if (v === undefined || v === null || v === "") continue;
    if (typeof v === "object" && !Array.isArray(v)) continue;
    out[k] = v;
    if (Object.keys(out).length >= 60) break;
  }
  return out;
}

interface FlatOptions {
  extraTimeKeys?: string[];
  overrides?: Partial<ParsedEvent>;
  parsedObjects?: number;
}

export function fromFieldMap(flat: Record<string, unknown>, raw: string, format: LogFormat, parser: string, opts: FlatOptions = {}): ParsedEvent {
  const timeKeys = [...TIME_KEYS, ...(opts.extraTimeKeys ?? [])];
  const timeField = firstOf(flat, timeKeys);
  const parsedTime: ParsedTime | undefined = timeField === undefined ? undefined : (parseTimestamp(timeField) ?? undefined);

  const level = stringOf(flat, LEVEL_KEYS);
  const severityId = opts.overrides?.severityId ?? (level ? severityFromLevel(level) : 0);

  const action = stringOf(flat, ACTION_KEYS);
  const statusText = stringOf(flat, STATUS_KEYS);
  const actionInfo = actionFromText(action);
  const statusInfo = statusFromText(statusText);
  const dispositionInfo = dispositionFromText(action ?? statusText);

  return {
    raw,
    format,
    parser,
    parsed_objects: opts.parsedObjects ?? 1,
    timestamp: parsedTime?.ms ?? opts.overrides?.timestamp,
    tzOffset: parsedTime?.tzOffset ?? opts.overrides?.tzOffset,
    message: opts.overrides?.message ?? stringOf(flat, MESSAGE_KEYS),
    level: level ?? opts.overrides?.level,
    severityId,
    statusId: opts.overrides?.statusId ?? statusInfo.status_id,
    actionId: opts.overrides?.actionId ?? actionInfo.action_id,
    dispositionId: opts.overrides?.dispositionId ?? dispositionInfo.disposition_id,
    srcIp: cleanIp(firstOf(flat, SRC_IP_KEYS)),
    srcPort: cleanPort(firstOf(flat, SRC_PORT_KEYS)),
    srcHost: stringOf(flat, SRC_HOST_KEYS),
    dstIp: cleanIp(firstOf(flat, DST_IP_KEYS)),
    dstPort: cleanPort(firstOf(flat, DST_PORT_KEYS)),
    dstHost: stringOf(flat, DST_HOST_KEYS),
    user: stringOf(flat, USER_KEYS) ?? opts.overrides?.user,
    userUid: stringOf(flat, USER_UID_KEYS),
    hostname: stringOf(flat, HOSTNAME_KEYS),
    appName: stringOf(flat, APP_KEYS) ?? opts.overrides?.appName,
    operation: stringOf(flat, OPERATION_KEYS),
    httpMethod: stringOf(flat, HTTP_METHOD_KEYS)?.toUpperCase(),
    httpStatus: httpStatusOf(flat),
    url: stringOf(flat, URL_KEYS),
    path: stringOf(flat, PATH_KEYS),
    userAgent: stringOf(flat, UA_KEYS),
    referrer: stringOf(flat, REFERRER_KEYS),
    protocol: stringOf(flat, PROTOCOL_KEYS)?.toLowerCase(),
    processName: stringOf(flat, PROCESS_NAME_KEYS),
    processId: cleanInt(firstOf(flat, PROCESS_ID_KEYS)),
    commandLine: stringOf(flat, CMD_LINE_KEYS),
    parentProcess: stringOf(flat, PARENT_PROCESS_KEYS),
    dnsQuery: stringOf(flat, DNS_QUERY_KEYS),
    dnsType: stringOf(flat, DNS_TYPE_KEYS),
    dnsAnswers: dnsAnswersOf(flat),
    dnsRcode: stringOf(flat, DNS_RCODE_KEYS),
    threat: stringOf(flat, THREAT_KEYS),
    ruleName: stringOf(flat, RULE_KEYS),
    eventType: stringOf(flat, EVENT_TYPE_KEYS),
    eventName: stringOf(flat, EVENT_NAME_KEYS),
    accountName: stringOf(flat, ACCOUNT_KEYS),
    authProtocol: stringOf(flat, AUTH_PROTOCOL_KEYS),
    logonType: stringOf(flat, LOGON_TYPE_KEYS),
    vendor: stringOf(flat, VENDOR_KEYS),
    product: stringOf(flat, PRODUCT_KEYS),
    vendorVersion: stringOf(flat, VERSION_KEYS),
    dbName: stringOf(flat, DB_KEYS),
    tableName: stringOf(flat, TABLE_KEYS),
    queryString: stringOf(flat, QUERY_STRING_KEYS),
    statusText,
    actionText: action,
    unmapped: unmappedOf(flat),
  };
}

// ---------------------------------------------------------------------------
// CEF (Common Event Format)
// ---------------------------------------------------------------------------

function unescapeCef(s: string): string {
  return s.replace(/\\([\\=|\n])/g, (_m, c: string) => (c === "n" ? "\n" : c));
}

function splitUnescaped(s: string, sep: string): string[] {
  const out: string[] = [];
  let cur = "";
  for (let i = 0; i < s.length; i++) {
    if (s[i] === "\\" && i + 1 < s.length) {
      cur += s[i] + s[i + 1];
      i++;
    } else if (s[i] === sep) {
      out.push(cur);
      cur = "";
    } else {
      cur += s[i];
    }
  }
  out.push(cur);
  return out;
}

export function parseKeyValuePairs(text: string): Record<string, string> {
  const out: Record<string, string> = {};
  let i = 0;
  const n = text.length;
  while (i < n) {
    while (i < n && /\s/.test(text[i])) i++;
    if (i >= n) break;
    const keyStart = i;
    while (i < n && /[^\s=]/.test(text[i])) i++;
    if (i >= n || text[i] !== "=" || i === keyStart) break;
    const key = text.slice(keyStart, i);
    i++; // skip '='
    let value = "";
    if (text[i] === '"') {
      i++;
      while (i < n && text[i] !== '"') {
        value += text[i] === "\\" && i + 1 < n && text[i + 1] === '"' ? '"' : text[i];
        if (text[i] === "\\" && i + 1 < n && text[i + 1] === '"') i++;
        i++;
      }
      i++; // closing quote
    } else {
      value = "";
      while (i < n) {
        if (/\s/.test(text[i])) {
          // A space followed by `word=` starts the next pair; anything else
          // (e.g. "Web Content", "1.2.3") is part of the current value.
          const ahead = /^\s+([a-zA-Z0-9_.-]+)=/.exec(text.slice(i));
          if (ahead) break;
          value += text[i];
          i++;
        } else {
          value += text[i];
          i++;
        }
      }
    }
    if (key !== "" && value !== "") out[key.toLowerCase()] = value;
  }
  return out;
}

function unescapeCefExtension(s: string): string {
  return s
    .replace(/\\([\\=|])/g, (_m, c: string) => c)
    .replace(/\\(\d)/g, (_m, d: string) => d); // CEF numeric escapes (e.g. \8 = backspace) are rare; keep text simple.
}

export function parseCefLine(line: string): ParsedEvent | null {
  const t = line.trim();
  const m = /^CEF:(?:\d)\|?(.*)$/.exec(t);
  if (!m) return null;
  const header = m[1].includes("|") ? m[1] : null;
  if (!header) return null;
  const fields = splitUnescaped(header, "|");
  if (fields.length < 6) return null;
  const [vendor, product, version, signatureId, name] = fields;
  const severity = fields[5];
  let messagePart: string | undefined;
  let extension: string | undefined;
  if (fields.length > 6) {
    // CEF header layout: Vendor|Product|Version|DeviceEventClassID|Name|Severity|[Message]|[Extension]
    const extIdx = fields.findIndex((f, i) => i >= 6 && f.includes("="));
    if (extIdx >= 0) {
      messagePart = cleanString(fields.slice(6, extIdx).join("|"));
      extension = fields.slice(extIdx).join("|");
    } else {
      messagePart = cleanString(fields.slice(6).join("|"));
    }
  }
  const flat: Record<string, unknown> = {};
  if (extension) {
    const kv = parseKeyValuePairs(unescapeCefExtension(extension));
    for (const [k, v] of Object.entries(kv)) flat[k.toLowerCase()] = v;
  }
  const time = firstOf(flat, ["rt", "start", "end", "eventTime"]);
  const msg = stringOf(flat, ["msg", "message"]) ?? cleanString(messagePart) ?? cleanString(name);
  const parsed = fromFieldMap(flat, line.trimEnd(), "cef", "CEF Parser", {
    extraTimeKeys: ["rt"],
    overrides: {
      timestamp: time === undefined ? undefined : parseTimestamp(cleanString(time))?.ms,
      message: cleanString(msg),
      severityId: severityFromCef(cleanString(severity)),
      appName: cleanString(product) ?? cleanString(vendor),
      vendor: cleanString(vendor) ?? undefined,
      product: cleanString(product) ?? undefined,
      vendorVersion: cleanString(version) ?? undefined,
    },
  });
  if (!parsed.message && !parsed.srcIp && !parsed.dstIp && !parsed.user && !parsed.eventType) return null;
  parsed.eventType = parsed.eventType ?? (cleanString(signatureId) ?? undefined);
  parsed.eventName = parsed.eventName ?? (cleanString(messagePart) ?? cleanString(name));
  parsed.vendor = parsed.vendor ?? cleanString(vendor);
  parsed.product = parsed.product ?? cleanString(product);
  parsed.vendorVersion = parsed.vendorVersion ?? cleanString(version);
  return parsed;
}

export function parseLeefLine(line: string): ParsedEvent | null {
  const t = line.trim();
  const m = /^LEEF:(?:[\d.]+)\|?(.*)$/.exec(t);
  if (!m) return null;
  const header = m[1].includes("|") ? m[1] : null;
  if (!header) return null;
  const parts = splitUnescaped(header, "|");
  if (parts.length < 5) return null;
  const [vendor, product, version, eventId, extension] = parts;
  void version;
  const flat: Record<string, unknown> = {};
  if (extension) {
    for (const [k, v] of Object.entries(parseKeyValuePairs(extension))) flat[k.toLowerCase()] = v;
  }
  const parsed = fromFieldMap(flat, line.trimEnd(), "leef", "LEEF Parser", {
    extraTimeKeys: ["devtime"],
    overrides: {
      severityId: severityFromCef(stringOf(flat, ["sev", "severity"])),
      appName: stringOf(flat, ["app"]) ?? cleanString(product),
      vendor: cleanString(vendor) ?? undefined,
      product: cleanString(product) ?? undefined,
      vendorVersion: stringOf(flat, ["version"]) ?? undefined,
    },
  });
  if (!parsed.message && !parsed.srcIp && !parsed.dstIp && !parsed.user) {
    const evtId = cleanString(eventId);
    if (!evtId) return null;
  }
  parsed.eventType = parsed.eventType ?? cleanString(eventId);
  return parsed;
}

// ---------------------------------------------------------------------------
// Syslog
// ---------------------------------------------------------------------------

export function parseSyslogLine(line: string): ParsedEvent | null {
  const t = line.trim();
  const priMatch = /^<(\d{1,3})>(.*)$/.exec(t);
  let severitySys: number | undefined;
  let facility: number | undefined;
  let rest = t;
  if (priMatch) {
    const pri = Number(priMatch[1]);
    if (Number.isInteger(pri) && pri >= 0 && pri <= 191) {
      facility = Math.floor(pri / 8);
      severitySys = pri % 8;
      rest = priMatch[2].trim();
    } else {
      return null;
    }
  }

  // RFC 5424: version digit then structured fields
  const v5424 = /^(\d) (.*)$/.exec(rest);
  if (v5424 && v5424[1] === "1") {
    const body = v5424[2];
    const m = /^(\S+) (\S+) (\S+) (\S+) (\S+) (\S+) (.+)$/.exec(body);
    if (m) {
      const [_, ts, host, app, procid, msgid, _sd, message] = m;
      void msgid;
      const time = parseTimestamp(ts);
      const appClean = cleanString(app);
      const procStr = cleanString(procid);
      const appNameVal =
        appClean && appClean !== "-"
          ? appClean
          : procStr && procStr !== "-" && cleanInt(procStr) === undefined
            ? procStr
            : undefined;
      const messageClean = cleanString(message);
      const parsedEvent: ParsedEvent = {
        raw: t,
        format: "syslog",
        parser: "Syslog Parser (RFC 5424)",
        parsed_objects: 1,
        timestamp: time?.ms,
        tzOffset: time?.tzOffset,
        severityId: severityFromSyslog(severitySys),
        srcHost: cleanString(host),
        hostname: cleanString(host),
        appName: appNameVal,
        processId: cleanInt(procid),
        message: messageClean,
        unmapped: {},
      };
      if (!parsedEvent.message) return null;
      return parsedEvent;
    }
    return null;
  }

  // RFC 3164 style: "Jan 22 12:43:02 host message" (optionally missing host)
  const m3164 = /^([A-Za-z]{3}\s{1,2}\d{1,2} \d{2}:\d{2}:\d{2})\s+(.*)$/.exec(rest);
  if (m3164) {
    const time = parseTimestamp(m3164[1]);
    const rest2 = m3164[2].trim();
    // first token may be a hostname
    const parts = rest2.split(/\s+/, 1);
    const first = parts[0] && cleanString(parts[0]);
    const hasHost = !!/^[a-z0-9._-]+$/i.exec(first ?? "") && (rest2.includes(" ") || isLikelyHost(first ?? ""));
    let host: string | undefined;
    let message = rest2;
    if (hasHost && first) {
      host = first;
      message = rest2.slice(first.length).trim();
    }
    if (!message) return null;
    return {
      raw: t,
      format: "syslog",
      parser: "Syslog Parser (RFC 3164)",
      parsed_objects: 1,
      timestamp: time?.ms,
      tzOffset: time?.tzOffset,
      severityId: severityFromSyslog(severitySys),
      hostname: host,
      srcHost: host,
      message: cleanString(message),
      unmapped: {},
    };
  }

  // Generic ISO-timestamped syslog: "2024-01-22T12:42:48Z hostname message"
  const mIso = /^(.*?\d{4}-\d{2}-\d{2}[T ]\d{1,2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)\s+(.*)$/.exec(rest);
  if (mIso) {
    const time = parseTimestamp(mIso[1]);
    if (time) {
      const rest3 = mIso[2].trim();
      const parts = rest3.split(/\s+/);
      const first = cleanString(parts[0]);
      let host: string | undefined;
      let message = rest3;
      if (first && isLikelyHost(first) && parts.length > 1) {
        host = first;
        message = rest3.slice(first.length).trim();
      }
      const messageClean = cleanString(message);
      if (!messageClean) return null;
      return {
        raw: t,
        format: "syslog",
        parser: "Syslog Parser",
        parsed_objects: 1,
        timestamp: time.ms,
        tzOffset: time.tzOffset,
        severityId: severityFromSyslog(severitySys),
        hostname: host,
        srcHost: host,
        message: messageClean,
        unmapped: {},
      };
    }
    return null;
  }

  return null;
}

function isLikelyHost(s: string): boolean {
  if (isIp(s)) return true;
  return /^[a-z0-9][a-z0-9._-]*$/i.test(s) && !/(error|failed|warn|info|debug|trace|timeout|denied|connection)/.test(s.toLowerCase()) && s.includes("-") === false;
}

// ---------------------------------------------------------------------------
// Apache / Nginx access logs
// ---------------------------------------------------------------------------

const APACHE_RE =
  /^(\S+)\s+(\S+)\s+(\S+)\s+\[([^\]]+)\]\s+"(\S+)\s+([^ "]+)(?:\s+([^"]+))?"\s+(\d{3})\s+(\S+)(?:\s+"([^"]*)")?(?:\s*"([^"]*)")?$/;

export function parseApacheLine(line: string): ParsedEvent | null {
  const t = line.trim();
  const m = APACHE_RE.exec(t);
  if (!m) return null;
  const time = parseTimestamp(`[${m[4]}]`);
  const status = cleanInt(m[8]);
  return {
    raw: t,
    format: "apache",
    parser: "HTTP Access Log Parser",
    parsed_objects: 1,
    timestamp: time?.ms,
    tzOffset: time?.tzOffset,
    severityId: status !== undefined && status >= 500 ? 4 : status !== undefined && status >= 400 ? 3 : 0,
    statusId: status !== undefined && status >= 400 ? 2 : 1,
    srcIp: cleanIp(m[1]),
    user: cleanString(m[3]),
    httpMethod: cleanString(m[5])?.toUpperCase(),
    url: cleanString(m[6]),
    path: cleanString(m[6])?.split("?")[0],
    httpStatus: status,
    referrer: cleanString(m[10]),
    userAgent: cleanString(m[11]),
    unmapped: {},
  };
}

// ---------------------------------------------------------------------------
// Key = value lines
// ---------------------------------------------------------------------------

const KV_PAIR_RE = /(?:^|\s)([a-zA-Z0-9_.-]+)=/g;

export function looksLikeKeyValue(line: string): boolean {
  const t = line.trim();
  if (/^("|\[|\{)/.test(t)) return false;
  const matches = [...t.matchAll(KV_PAIR_RE)].length;
  return matches >= 2;
}

export function parseKeyValueLine(line: string): ParsedEvent | null {
  const t = line.trim();
  if (!looksLikeKeyValue(t)) return null;
  // A structured kv value could still start with a syslog prefix; syslog never contains '=' though.
  const flat: Record<string, unknown> = {};
  const kv = parseKeyValuePairs(t);
  for (const [k, v] of Object.entries(kv)) flat[k.toLowerCase()] = v;
  if (Object.keys(flat).length < 2) return null;
  return fromFieldMap(flat, t, "keyvalue", "Key/Value Parser", { extraTimeKeys: ["rt"] });
}

// ---------------------------------------------------------------------------
// Plain text
// ---------------------------------------------------------------------------

const CLEAN_TEXT_RE = /^[0-9a-zA-Z_.-]+$/;
const LOG_SIGNAL_RE =
  /(error|failure|fail|failed|warn|warning|info|informational|debug|trace|fatal|critical|panic|alert|emergency|denied|reject|refused|block|blocked|allow|allowed|accept|login|logon|logout|auth|authenticat|timeout|attack|exploit|threat|malware|ransomware|phish|phishing|success|successful|started|stopped|restart|install|remove|update|creat|delet|read|write|query|scan|detect|violation|unauthor|forbidden|permission|privilege|ssl|session|password|user|account|connection|disconnect|request|response|invalid|exception|segfault|oops|ip6|ipv4|firewall|severe|notice|unexpected)/i;

export function parseTextLine(line: string): ParsedEvent | null {
  const t = line.trim();
  const timeMatch = /^(\d{4}-\d{2}-\d{2}[T ]\d{1,2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?|[A-Za-z]{3}\s{1,2}\d{1,2} \d{2}:\d{2}:\d{2})\s+(.*)$/.exec(t);
  let timestamp: number | undefined;
  let tzOffset: number | undefined;
  let message = t;
  if (timeMatch) {
    const time = parseTimestamp(timeMatch[1]);
    if (time) {
      timestamp = time.ms;
      tzOffset = time.tzOffset;
      message = timeMatch[2].trim();
    }
  }
  if (CLEAN_TEXT_RE.test(t)) return null; // a bare token/word, not a real log line
  const msg = cleanString(message);
  if (!msg) return null;
  // Without a timestamp a free-text line must carry log vocabulary, otherwise it is
  // indistinguishable from prose and would only introduce noise into the OCSF feed.
  if (!timestamp && !LOG_SIGNAL_RE.test(msg)) return null;
  return {
    raw: t,
    format: "text",
    parser: "Text Parser",
    parsed_objects: 1,
    timestamp,
    tzOffset,
    message: msg,
    unmapped: {},
  };
}

// ---------------------------------------------------------------------------
// Dispatch
// ---------------------------------------------------------------------------

export function detectFormat(line: string): LogFormat {
  const t = line.trim();
  if (t.startsWith("{") || t.startsWith("[")) return "json";
  if (/^CEF:/i.test(t)) return "cef";
  if (/^LEEF:/i.test(t)) return "leef";
  if (/^<\d{1,3}>/.test(t)) return "syslog";
  if (/^\S+\s+\S+\s+\S+\s+\[\d{2}\/[A-Za-z]{3}\/\d{4}:\d{2}:\d{2}:\d{2}\s[+-]\d{4}\]\s+"/.test(t)) return "apache";
  if (/^\d{4}-\d{2}-\d{2}[T ]\d{1,2}:\d{2}:\d{2}/.test(t)) return "syslog";
  if (/^[A-Za-z]{3}\s{1,2}\d{1,2} \d{2}:\d{2}:\d{2}/.test(t)) return "syslog";
  if (looksLikeKeyValue(t)) return "keyvalue";
  return "text";
}

export function parseJsonLine(line: string): ParsedEvent[] | null {
  const t = line.trim();
  if (!(t.startsWith("{") || t.startsWith("["))) return null;
  let value: unknown;
  try {
    value = JSON.parse(t);
  } catch {
    return null;
  }
  if (value === null || typeof value !== "object") return null;
  const items = Array.isArray(value) ? value : [value];
  const out: ParsedEvent[] = [];
  for (const item of items) {
    if (item === null || typeof item !== "object") continue;
    const flat = flattenJson(item);
    out.push(fromFieldMap(flat, t, "json", "JSON Parser", { parsedObjects: items.length }));
  }
  return out.length > 0 ? out : null;
}