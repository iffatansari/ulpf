import { OcsfEvent } from "../../shared/api";

/**
 * Static OCSF 1.3.0 reference tables used to build normalized events.
 * Only values verified against the published OCSF schema are used here.
 */
export const OCSF_VERSION = "1.3.0";

export const SEVERITY: Record<number, string> = {
  0: "Unknown",
  1: "Informational",
  2: "Low",
  3: "Medium",
  4: "High",
  5: "Critical",
  6: "Fatal",
  99: "Other",
};

export const STATUS: Record<number, string> = {
  0: "Unknown",
  1: "Success",
  2: "Failure",
  99: "Other",
};

export const ACTION: Record<number, string> = {
  0: "Unknown",
  1: "Allowed",
  2: "Denied",
  99: "Other",
};

export const DISPOSITION: Record<number, string> = {
  0: "Unknown",
  1: "Allowed",
  2: "Blocked",
  3: "Quarantined",
  4: "Isolated",
  5: "Deleted",
  6: "Dropped",
  7: "Custom Action",
  8: "Approved",
  9: "Restored",
  10: "Exonerated",
  11: "Corrected",
  12: "Partially Corrected",
  13: "Uncorrected",
  14: "Delayed",
  15: "Detected",
  16: "No Action",
  17: "Logged",
  18: "Tagged",
  19: "Alert",
  20: "Count",
  21: "Reset",
  22: "Captcha",
  23: "Challenge",
  24: "Access Revoked",
  25: "Rejected",
  26: "Unauthorized",
  27: "Error",
  99: "Other",
};

export interface OcsfClass {
  class_uid: number;
  class_name: string;
  category_uid: number;
  category_name: string;
  activities: Record<number, string>;
}

export type ClassKey =
  | "base_event"
  | "process_activity"
  | "detection_finding"
  | "account_change"
  | "authentication"
  | "user_access"
  | "network_activity"
  | "http_activity"
  | "dns_activity"
  | "application_lifecycle"
  | "api_activity"
  | "datastore_activity";

export const OCSF_CLASSES: Record<ClassKey, OcsfClass> = {
  base_event: {
    class_uid: 0,
    class_name: "Base Event",
    category_uid: 0,
    category_name: "Uncategorized",
    activities: { 0: "Unknown" },
  },
  process_activity: {
    class_uid: 1007,
    class_name: "Process Activity",
    category_uid: 1,
    category_name: "System Activity",
    activities: { 0: "Unknown", 1: "Launch", 2: "Terminate", 3: "Open", 4: "Inject", 5: "Set User ID" },
  },
  detection_finding: {
    class_uid: 2004,
    class_name: "Detection Finding",
    category_uid: 2,
    category_name: "Findings",
    activities: { 0: "Unknown", 1: "Create", 2: "Update", 3: "Close" },
  },
  account_change: {
    class_uid: 3001,
    class_name: "Account Change",
    category_uid: 3,
    category_name: "Identity & Access Management",
    activities: {
      0: "Unknown",
      1: "Create",
      2: "Enable",
      3: "Password Change",
      4: "Password Reset",
      5: "Disable",
      6: "Delete",
      7: "Attach Policy",
      8: "Detach Policy",
      9: "Lock",
      10: "MFA Factor Enable",
      11: "MFA Factor Disable",
    },
  },
  authentication: {
    class_uid: 3002,
    class_name: "Authentication",
    category_uid: 3,
    category_name: "Identity & Access Management",
    activities: { 0: "Unknown", 1: "Logon", 2: "Logoff", 3: "Authentication Ticket", 4: "Service Ticket Request", 5: "Service Ticket Renew", 6: "Preauth" },
  },
  user_access: {
    class_uid: 3005,
    class_name: "User Access Management",
    category_uid: 3,
    category_name: "Identity & Access Management",
    activities: { 0: "Unknown", 1: "Assign Privileges", 2: "Revoke Privileges" },
  },
  network_activity: {
    class_uid: 4001,
    class_name: "Network Activity",
    category_uid: 4,
    category_name: "Network Activity",
    activities: { 0: "Unknown", 1: "Open", 2: "Close", 3: "Reset", 4: "Fail", 5: "Refuse", 6: "Traffic", 7: "Listen" },
  },
  http_activity: {
    class_uid: 4002,
    class_name: "HTTP Activity",
    category_uid: 4,
    category_name: "Network Activity",
    activities: { 0: "Unknown", 1: "Connect", 2: "Delete", 3: "Get", 4: "Head", 5: "Options", 6: "Post", 7: "Put", 8: "Trace" },
  },
  dns_activity: {
    class_uid: 4003,
    class_name: "DNS Activity",
    category_uid: 4,
    category_name: "Network Activity",
    activities: { 0: "Unknown", 1: "Query", 2: "Response", 6: "Traffic" },
  },
  application_lifecycle: {
    class_uid: 6002,
    class_name: "Application Lifecycle",
    category_uid: 6,
    category_name: "Application Activity",
    activities: { 0: "Unknown", 1: "Install", 2: "Remove", 3: "Start", 4: "Stop", 5: "Restart", 6: "Enable", 7: "Disable", 8: "Update" },
  },
  api_activity: {
    class_uid: 6003,
    class_name: "API Activity",
    category_uid: 6,
    category_name: "Application Activity",
    activities: { 0: "Unknown", 1: "Create", 2: "Read", 3: "Update", 4: "Delete" },
  },
  datastore_activity: {
    class_uid: 6005,
    class_name: "Datastore Activity",
    category_uid: 6,
    category_name: "Application Activity",
    activities: { 0: "Unknown", 1: "Read", 2: "Update", 3: "Connect", 4: "Query", 5: "Write", 6: "Create", 7: "Delete", 8: "List", 9: "Encrypt", 10: "Decrypt" },
  },
};

/**
 * Text level (as seen in app/syslog semantics) -> OCSF severity_id.
 */
const LEVEL_TO_SEVERITY: Record<string, number> = {
  emergency: 6,
  emerg: 6,
  panic: 6,
  fatal: 6,
  critical: 5,
  crit: 5,
  alert: 5,
  severe: 5,
  error: 4,
  err: 4,
  failure: 4,
  fail: 4,
  warning: 3,
  warn: 3,
  notice: 2,
  informational: 1,
  info: 1,
  debug: 1,
  trace: 1,
  verbose: 1,
};

export function severityFromLevel(level?: string | null): number {
  if (!level) return 0;
  const key = level.toLowerCase().trim();
  if (key in LEVEL_TO_SEVERITY) return LEVEL_TO_SEVERITY[key];
  const n = Number(key);
  if (Number.isInteger(n) && n >= 0 && n <= 6) return n;
  return 0;
}

/**
 * RFC 5424 / syslog severity level (0-7).
 */
export function severityFromSyslog(pri: string | number | undefined, levelHint?: string): number {
  if (pri !== undefined && pri !== null && pri !== "") {
    const n = Number(pri);
    if (Number.isInteger(n)) return [6, 5, 5, 4, 3, 2, 1, 1][n] ?? 0;
  }
  return severityFromLevel(levelHint);
}

/**
 * CEF severity (0-10) -> OCSF severity_id.
 */
export function severityFromCef(value: string | undefined): number {
  if (value === undefined || value === null || value === "") return 0;
  const n = Number(value);
  if (!Number.isInteger(n)) return severityFromLevel(value);
  if (n <= 3) return 2;
  if (n <= 6) return 3;
  if (n <= 9) return 4;
  if (n === 10) return 5;
  return 0;
}

const SUCCESS_WORDS = ["success", "succeeded", "successful", "accepted", "allow", "allowed", "permit", "permitted", "ok", "pass", "passed", "enabled", "created", "added", "granted", "available", "up", "normal", "clean"];
const FAILURE_WORDS = ["fail", "failed", "failure", "deny", "denied", "block", "blocked", "drop", "dropped", "reject", "rejected", "refuse", "refused", "error", "fault", "forbidden", "unavailable", "down", "inactive", "disabled", "timedout", "timeout", "invalid", "unauthorized"];

export function statusFromText(text?: string | null): { status_id: number; status?: string } {
  if (!text) return { status_id: 0 };
  const t = text.toLowerCase().trim();
  if (SUCCESS_WORDS.includes(t)) return { status_id: 1, status: "Success" };
  if (FAILURE_WORDS.includes(t)) return { status_id: 2, status: "Failure" };
  return { status_id: 0 };
}

export function actionFromText(text?: string | null): { action_id: number; action?: string } {
  if (!text) return { action_id: 0 };
  const t = text.toLowerCase().trim();
  if (["allow", "allowed", "permit", "permitted", "accept", "accepted", "bypass", "yes"].includes(t)) return { action_id: 1, action: "Allowed" };
  if (["deny", "denied", "block", "blocked", "drop", "dropped", "reject", "rejected", "refuse", "refused", "close", "closed"].includes(t)) return { action_id: 2, action: "Denied" };
  return { action_id: 0 };
}

const DISPOSITION_WORDS: Record<string, number> = {
  blocked: 2,
  quarantine: 3,
  quarantined: 3,
  isolated: 4,
  deleted: 5,
  dropped: 6,
  approved: 8,
  restored: 9,
  corrected: 11,
  delayed: 14,
  detected: 15,
  logged: 17,
  alert: 19,
  alerted: 19,
  reset: 21,
  rejected: 25,
  unauthorized: 26,
  error: 27,
  allowed: 1,
  permitted: 1,
};

export function dispositionFromText(text?: string | null): { disposition_id: number; disposition?: string } {
  if (!text) return { disposition_id: 0 };
  const t = text.toLowerCase().trim();
  const id = DISPOSITION_WORDS[t];
  if (id !== undefined) {
    const name = DISPOSITION[id];
    return { disposition_id: id, disposition: name };
  }
  return { disposition_id: 0 };
}

export interface BaseEventInput {
  cls: OcsfClass;
  activityId: number;
  severityId: number;
  time: number;
  timezoneOffset?: number;
  message?: string;
  appName?: string;
  metadataLog?: { name?: string; provider?: string; product?: string; version?: string };
  labels?: string[];
}

/**
 * Builds a minimal, schema-valid OCSF event envelope. The only values written
 * are real parsed values — nothing is ever fabricated.
 */
export function buildBaseEvent(input: BaseEventInput): OcsfEvent {
  const activityName = input.cls.activities[input.activityId] ?? "Other";
  const event: OcsfEvent = {
    activity_id: input.activityId,
    activity_name: activityName === "Unknown" ? undefined : activityName,
    category_uid: input.cls.category_uid,
    category_name: input.cls.category_name,
    class_uid: input.cls.class_uid,
    class_name: input.cls.class_name,
    severity_id: input.severityId,
    severity: SEVERITY[input.severityId] === "Unknown" ? undefined : SEVERITY[input.severityId],
    type_uid: input.cls.class_uid * 100 + input.activityId,
    type_name: `${input.cls.class_name}: ${activityName}`,
    time: input.time,
    metadata: {
      version: OCSF_VERSION,
      labels: input.labels && input.labels.length > 0 ? input.labels : undefined,
      log: input.metadataLog && Object.keys(input.metadataLog).length > 0 ? input.metadataLog : undefined,
    },
  };
  if (input.timezoneOffset !== undefined && input.timezoneOffset !== null) event.timezone_offset = input.timezoneOffset;
  const msg = input.message?.trim();
  if (msg) event.message = msg;
  if (input.appName) event.app_name = input.appName;
  return event;
}

export function withTimeDt(event: OcsfEvent): OcsfEvent {
  if (!event.time_dt) event.time_dt = new Date(event.time).toISOString();
  return event;
}