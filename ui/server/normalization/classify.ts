import { OcsfEvent } from "../../shared/api";
import { ParsedEvent, cleanString } from "./parsers";
import { ClassKey, OCSF_CLASSES, buildBaseEvent, dispositionFromText, statusFromText, withTimeDt } from "./ocsf";

/**
 * Maps a ParsedEvent to an OCSF class and activity using only real, parsed
 * signal. Falls back to the Base Event class when nothing trustworthy provides
 * a class; Base Event carries whatever fields were cleanly parsed.
 */

interface ClassifiedEvent {
  cls: ClassKey;
  activityId: number;
}

const DNS_RCODE: Record<string, number> = {
  noerror: 0,
  formerr: 1,
  servfail: 2,
  nxdomain: 3,
  notimpl: 4,
  refused: 5,
  yxdomain: 6,
  yxrrset: 7,
  nxrrset: 8,
  notauth: 9,
  notzone: 10,
};

const HTTP_METHOD_ACTIVITY: Record<string, number> = {
  GET: 3,
  POST: 6,
  PUT: 7,
  DELETE: 2,
  HEAD: 4,
  OPTIONS: 5,
  TRACE: 8,
  CONNECT: 1,
  PATCH: 0,
};

function blobOf(parsed: ParsedEvent): string {
  return [parsed.message, parsed.eventName, parsed.eventType, parsed.statusText, parsed.actionText, parsed.threat, parsed.ruleName, parsed.operation]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
}

function hasTokens(text: string, tokens: string[]): boolean {
  return tokens.some((t) => new RegExp(`(^|[^a-z0-9])${escapeRegex(t)}([^a-z0-9]|$)`).test(text));
}

function hasPhrases(text: string, phrases: string[]): boolean {
  return phrases.some((p) => text.includes(p));
}

function escapeRegex(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function classify(parsed: ParsedEvent): ClassifiedEvent {
  const blob = blobOf(parsed);

  // DNS Activity (4003)
  if (parsed.dnsQuery || parsed.dnsType || parsed.dnsAnswers || parsed.dnsRcode) {
    let activityId = 0;
    if (hasTokens(blob, ["response", "answer"]) || parsed.dnsRcode) activityId = 2;
    else if (hasTokens(blob, ["query", "request", "resolve", "lookup"])) activityId = 1;
    return { cls: "dns_activity", activityId };
  }

  // HTTP Activity (4002)
  if (parsed.httpMethod || parsed.httpStatus !== undefined) {
    const activityId = parsed.httpMethod ? (HTTP_METHOD_ACTIVITY[parsed.httpMethod.toUpperCase()] ?? 0) : 0;
    return { cls: "http_activity", activityId };
  }

  // API Activity (6003)
  if (parsed.operation) {
    const op = parsed.operation.toLowerCase();
    let activityId = 0;
    if (hasTokens(op, ["delete", "remove", "destroy", "terminate"])) activityId = 4;
    else if (hasTokens(op, ["update", "edit", "modify", "set", "put", "patch", "replace", "attach", "detach"])) activityId = 3;
    else if (hasTokens(op, ["create", "add", "new", "insert", "make", "post", "initiate", "start"])) activityId = 1;
    else if (hasTokens(op, ["get", "read", "list", "describe", "query", "download", "search", "head", "info"])) activityId = 2;
    return { cls: "api_activity", activityId };
  }

  // Authentication (3002)
  const authFieldsPresent = !!(parsed.authProtocol || parsed.logonType);
  const authWords = ["logon", "login", "signon", "preauth", "kerberos", "authentication", "authenticated", "authenticating", "oath", "mfa", "multi-factor", "4624", "4625", "4768", "4771", "4776"];
  const authPhrases = ["failed password", "password for", "invalid user", "login attempt", "login from", "logged in", "logged on", "logon", "not authorized"];
  const authEventTypes = /^(audit_success|audit_failure|security_success|security_failure|Failed|Success Audit|Failure Audit)$/;
  if (authFieldsPresent || hasTokens(blob, authWords) || hasPhrases(blob, authPhrases) || authEventTypes.test(parsed.eventType ?? "")) {
    const activityId = /(logoff|logout|signoff|signed out|logged out)/.test(blob) ? 2 : 1;
    return { cls: "authentication", activityId };
  }

  // User Access Management (3005)
  const accessPhrases = [
    "assign privilege",
    "assign privileges",
    "privilege assigned",
    "privilege added",
    "grant privilege",
    "grant privileges",
    "privilege granted",
    "role assigned",
    "added role",
    "privilege escalation",
    "privilege level",
    "revoke privilege",
    "revoke privileges",
    "privilege revoked",
    "privilege removed",
    "remove role",
    "revoked role",
  ];
  if (hasPhrases(blob, accessPhrases)) {
    const activityId = /revok|remove/i.test(blob) ? 2 : 1;
    return { cls: "user_access", activityId };
  }

  // Account Change (3001)
  const accountPhrases = [
    "password changed",
    "change password",
    "password change",
    "password reset",
    "reset password",
    "password was reset",
    "account created",
    "user created",
    "account added",
    "user added",
    "user deleted",
    "account deleted",
    "user removed",
    "account disabled",
    "user disabled",
    "account enabled",
    "user enabled",
    "account locked",
    "locked out",
    "unlocked",
    "account unlock",
    "member added",
    "member removed",
    "group added",
    "group removed",
  ];
  const accountEventIds = ["4720", "4722", "4723", "4724", "4725", "4726", "4735", "4736", "4738", "4740", "4754", "4755", "4756", "4762", "4741", "4742", "4743"];
  if (hasPhrases(blob, accountPhrases) || (parsed.eventType !== undefined && accountEventIds.includes(parsed.eventType))) {
    let activityId = 0;
    if (/disable|disabled|delete|deleted|remove|removed|unlock/.test(blob) && /disable|disabled|delete|deleted|remove|removed/.test(blob)) activityId = /disable|disabled/.test(blob) ? 5 : /delete|deleted|remove|removed/.test(blob) ? 6 : 0;
    else if (/password change|change password|password changed/.test(blob)) activityId = 3;
    else if (/password reset|reset password|password was reset/.test(blob)) activityId = 4;
    else if (/enable|enabled/.test(blob)) activityId = 2;
    else if (/lock/.test(blob)) activityId = 9;
    else if (/create|added|new/.test(blob)) activityId = 1;
    return { cls: "account_change", activityId };
  }

  // Detection Finding (2004)
  const detectionWords = ["malware", "ransomware", "spyware", "trojan", "virus", "intrusion", "exploit", "phish", "phishing", "quarant", "cobalt", "behavioral", "command and control", "c2 beacon", "cve-"];
  const detectionEventIds = ["4662", "4663", "8004", "8006"];
  if (parsed.threat || hasTokens(blob, detectionWords) || (parsed.eventType !== undefined && detectionEventIds.includes(parsed.eventType))) {
    let activityId = 1;
    if (/close|closed/.test(blob)) activityId = 3;
    else if (/update|updated|change|changed|suppress/.test(blob)) activityId = 2;
    return { cls: "detection_finding", activityId };
  }

  // Process Activity (1007)
  if (parsed.processName || parsed.processId !== undefined) {
    let activityId = 0;
    if (/terminate|stop|kill|exit|ending/.test(blob)) activityId = 2;
    else if (/launch|start|exec|spawn|begin/.test(blob)) activityId = 1;
    else if (/open|opening/.test(blob)) activityId = 3;
    return { cls: "process_activity", activityId };
  }

  // Network Activity (4001)
  if (parsed.srcIp || parsed.dstIp || parsed.protocol || parsed.ruleName) {
    let activityId = 0;
    if (/close|closed|closedown/.test(blob)) activityId = 2;
    else if (/reset|rst/.test(blob) && !/open/.test(blob)) activityId = 3;
    else if (/fail|error|refused|refuse|deny|denied|block|blocked|drop|dropped|reject|rejected/.test(blob)) activityId = 5;
    else if (/listen|listening/.test(blob)) activityId = 7;
    else if (/open|accepted|accept|allow|allowed|established|syn/.test(blob)) activityId = 1;
    else activityId = 6; // Traffic — generic network activity
    return { cls: "network_activity", activityId };
  }

  // Datastore Activity (6005)
  if (parsed.dbName || parsed.tableName || parsed.queryString) {
    let activityId = 0;
    const q = (parsed.queryString ?? "").toLowerCase().trim();
    const first = q.split(/\s+/)[0] ?? "";
    if (/^(select|get|read|fetch)/.test(q)) activityId = 1;
    else if (/^(delete|drop)/.test(q) || /^delete/.test(first)) activityId = 7;
    else if (/^(insert|write|replace|merge)/.test(q)) activityId = 5;
    else if (/^(update|set|alter|modify)/.test(q)) activityId = 2;
    else if (/^(create|make|new)/.test(q)) activityId = 6;
    else if (/^(connect|open)/.test(q)) activityId = 3;
    else if (/^(list|show|describe|explain)/.test(q)) activityId = 8;
    else if (/^(encrypt)/.test(q)) activityId = 9;
    else if (/^(decrypt)/.test(q)) activityId = 10;
    else if (/^(query|search|find)/.test(q) || /select|from/.test(q)) activityId = 4;
    if (activityId === 0 && /^(select|get|read|fetch|query)/.test(first)) activityId = 1;
    return { cls: "datastore_activity", activityId };
  }

  // Application Lifecycle (6002)
  if (parsed.appName) {
    let activityId = 0;
    if (/disable|disabled/.test(blob)) activityId = 7;
    else if (/enable|enabled/.test(blob) && !/disable|disabled/.test(blob)) activityId = 6;
    else if (/restart|reload/.test(blob)) activityId = 5;
    else if (/stop|stopped|shutdown|shut down/.test(blob)) activityId = 4;
    else if (/start|started|launch|launched/.test(blob)) activityId = 3;
    else if (/uninstall|remove|removed|delete|deleted/.test(blob)) activityId = 2;
    else if (/install|installed|update|updated|upgrade|upgraded|patch/.test(blob)) activityId = 8;
    return { cls: "application_lifecycle", activityId };
  }

  // No trustworthy class -> Base Event
  return { cls: "base_event", activityId: 0 };
}

function urlHostname(url: string | undefined): string | undefined {
  if (!url) return undefined;
  try {
    return new URL(url).hostname;
  } catch {
    const m = /^[a-z][a-z0-9+.-]*:\/\/([^/?#]+)/i.exec(url);
    return m ? m[1] : undefined;
  }
}

function rcodeId(text: string | undefined): { id?: number; name?: string } {
  const s = cleanString(text);
  if (!s) return {};
  const key = s.toLowerCase();
  const id = DNS_RCODE[key];
  if (id !== undefined) return { id, name: s.toUpperCase() };
  return {};
}

/**
 * Builds the full OCSF event for a single parsed line. Returns null only when
 * the parsed event carries no usable content (should not normally happen, the
 * pipeline rejects earlier).
 */
export function classifyParsed(parsed: ParsedEvent, ingestTime: number, sourceName?: string): OcsfEvent | null {
  if (!parsed.raw) return null;
  const { cls, activityId } = classify(parsed);
  const ocsfCls = OCSF_CLASSES[cls];

  const labels = [`format:${parsed.format}`];
  if (parsed.timestamp === undefined) labels.push("no_timestamp_in_source");

  const time = parsed.timestamp ?? ingestTime;
  const severityId = parsed.severityId ?? 0;

  const statusResolved = statusFromText(parsed.statusText);

  const event: OcsfEvent = buildBaseEvent({
    cls: ocsfCls,
    activityId,
    severityId,
    time,
    timezoneOffset: parsed.tzOffset,
    message: parsed.message,
    appName: parsed.appName,
    labels,
    metadataLog: {
      name: sourceName,
      provider: parsed.vendor,
      product: parsed.product,
      version: parsed.vendorVersion,
    },
  });

  if (parsed.timestamp === undefined) delete event.time_dt;

  const status = parsed.statusId !== undefined && parsed.statusId !== 0
    ? { status_id: parsed.statusId, status: statusResolved.status ?? (parsed.statusId === 1 ? "Success" : "Failure") }
    : statusResolved;
  if (status.status_id !== 0) {
    event.status_id = status.status_id;
    event.status = status.status;
  }
  if (parsed.actionId !== undefined && parsed.actionId !== 0) {
    event.action_id = parsed.actionId;
    event.action = parsed.actionId === 1 ? "Allowed" : "Denied";
  }
  if (parsed.dispositionId !== undefined && parsed.dispositionId !== 0) {
    const disp = dispositionFromText(parsed.actionText ?? parsed.statusText);
    event.disposition_id = parsed.dispositionId;
    event.disposition = disp.disposition;
  }

  if (parsed.dnsQuery) event.query = { hostname: parsed.dnsQuery.toLowerCase(), type: cleanString(parsed.dnsType)?.toUpperCase() };
  else if (parsed.dnsType) event.query = { type: cleanString(parsed.dnsType)?.toUpperCase() };
  if (parsed.dnsAnswers) event.answers = parsed.dnsAnswers.map((ip) => ({ ip }));
  if (parsed.dnsRcode) {
    const rc = rcodeId(parsed.dnsRcode);
    if (rc.id !== undefined) {
      event.rcode_id = rc.id;
      event.rcode = rc.name;
    }
  }

  const src = endpointOf(parsed.srcIp, parsed.srcPort, parsed.srcHost);
  const dst = endpointOf(parsed.dstIp, parsed.dstPort, parsed.dstHost);
  if (src) event.src_endpoint = src;
  if (dst) event.dst_endpoint = dst;
  if (parsed.hostname || parsed.srcHost) {
    event.device = {};
    const hostname = cleanString(parsed.hostname ?? parsed.srcHost);
    if (hostname && hostname !== "-") event.device.hostname = hostname;
    if (!event.device.hostname) delete event.device;
  }
  if (parsed.user || parsed.userUid) {
    event.user = {};
    if (parsed.userUid) event.user.uid = parsed.userUid;
    if (parsed.user) event.user.name = parsed.user;
    event.actor = { user: { ...event.user } };
  }
  if (parsed.processName || parsed.processId !== undefined) {
    event.process = {};
    if (parsed.processName) event.process.name = parsed.processName;
    if (parsed.processId !== undefined && parsed.processId !== 0) event.process.pid = parsed.processId;
    if (parsed.commandLine) event.process.cmd_line = parsed.commandLine;
    if (!event.process.name && !event.process.pid && !event.process.cmd_line) delete event.process;
  }
  if (parsed.httpMethod || parsed.httpStatus !== undefined) {
    event.http_request = {};
    const method = cleanString(parsed.httpMethod)?.toUpperCase();
    if (method) event.http_request.method = method;
    if (parsed.userAgent) event.http_request.user_agent = parsed.userAgent;
    const url = parsed.url ?? parsed.path;
    if (url) {
      event.http_request.url = {};
      const hostname = urlHostname(url);
      if (hostname) event.http_request.url.hostname = hostname;
      event.http_request.url.path = parsed.path;
      event.http_request.url.url_string = parsed.url;
      if (!event.http_request.url.url_string && !event.http_request.url.hostname) delete event.http_request.url;
    }
    if (parsed.httpStatus !== undefined) event.http_response = { code: parsed.httpStatus };
    if (!event.http_request.method && !event.http_request.user_agent && !event.http_request.url) delete event.http_request;
  }
  if (parsed.operation) {
    event.api = {};
    if (parsed.operation) event.api.operation = parsed.operation;
    if (parsed.vendor || parsed.product) {
      event.api.service = {};
      if (parsed.product) event.api.service.name = parsed.product;
      else if (parsed.vendor) event.api.service.name = parsed.vendor;
    }
  }
  if (parsed.protocol) {
    event.connection_info = {};
    const proto = parsed.protocol.toLowerCase();
    event.connection_info.protocol_name = proto;
    const num = protocolNum(proto);
    if (num !== undefined) event.connection_info.protocol_num = num;
  }
  if (parsed.ruleName) event.firewall_rule = { name: parsed.ruleName };
  if (parsed.dbName) event.database = { name: parsed.dbName };
  if (parsed.tableName) event.table = { name: parsed.tableName };
  if (parsed.queryString) event.query_info = { query_string: parsed.queryString };

  if (Object.keys(parsed.unmapped).length > 0) event.unmapped = parsed.unmapped;
  event.raw_data = parsed.raw;

  withTimeDt(event);

  // OCSF convention: append profile/observable hygiene is out of scope; the
  // event above only references attributes verified in OCSF 1.3.0.
  return event;
}

function endpointOf(ip: string | undefined, port: number | undefined, hostname: string | undefined): OcsfEvent["src_endpoint"] | undefined {
  if (!ip && port === undefined && !hostname) return undefined;
  const e: NonNullable<OcsfEvent["src_endpoint"]> = {};
  if (ip) e.ip = ip;
  if (port !== undefined && port !== 0) e.port = port;
  const h = cleanString(hostname);
  if (h && h !== "-") e.hostname = h;
  return Object.keys(e).length > 0 ? e : undefined;
}

function protocolNum(name: string): number | undefined {
  const tcp443 = { tcp: 6, udp: 17, icmp: 1, ipv6: 41, sctp: 132, gre: 47 };
  const key = name.toLowerCase();
  const hit = (tcp443 as Record<string, number>)[key];
  return hit;
}