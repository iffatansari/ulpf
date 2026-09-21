// Client-side reference copy of the OCSF 1.3.0 tables used by the normalizer.
// Mirrors server/normalization/ocsf.ts so the schema pages render real values.

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
  base_event: { class_uid: 0, class_name: "Base Event", category_uid: 0, category_name: "Uncategorized", activities: { 0: "Unknown" } },
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

export const CLASS_LIST: OcsfClass[] = Object.values(OCSF_CLASSES);

export interface ClassAttr {
  name: string;
  type: string;
  description: string;
}

// Curated core attributes per class — the fields our normalizer is able to populate.
export const CLASS_ATTRS: Record<string, ClassAttr[]> = {
  "Base Event": [
    { name: "time", type: "integer", description: "Event timestamp as epoch milliseconds. Falling back to ingest time is labelled no_timestamp_in_source." },
    { name: "class_uid", type: "integer", description: "Unique class id (getter)." },
    { name: "category_uid", type: "integer", description: "Category id (getter)." },
    { name: "type_uid", type: "integer", description: "class_uid * 100 + activity_id." },
    { name: "severity_id", type: "integer", description: "Normalized severity — never fabricated, defaults to Unknown (0)." },
    { name: "status_id", type: "integer", description: "Derived only when the line explicitly says e.g. success / failed." },
    { name: "message", type: "string", description: "The raw human-readable message when present." },
    { name: "metadata.version", type: "string", description: "OCSF schema version, always 1.3.0." },
    { name: "metadata.log", type: "object", description: "Source name / provider / product / version taken from the parsed header fields." },
    { name: "unmapped", type: "object", description: "Real fields that do not fit the chosen class — surfaced instead of being dropped." },
    { name: "raw_data", type: "string", description: "The original source line, verbatim." },
  ],
  "Process Activity": [
    { name: "process", type: "Process", description: "Executable, name, pid, cmd_line and path when present in the line." },
    { name: "actor.user", type: "User", description: "Account that launched or owns the process." },
    { name: "src_endpoint", type: "Endpoint", description: "Host the process is running on when log provides it." },
    { name: "activity_id", type: "integer", description: "Launch (1) / Terminate (2) / Open (3) / Inject (4) / Set User ID (5)." },
  ],
  "Detection Finding": [
    { name: "finding", type: "Detection Rule Finding", description: "Rule name, uid and type parsed from the alert line." },
    { name: "evidence", type: "Finding Evidence", description: "Src / dst endpoints, user, evidences observed by the rule." },
    { name: "disposition_id", type: "integer", description: "Blocked / detected / alerting outcome for the finding." },
    { name: "severity_id", type: "integer", description: "Rule severity mapped from the vendor level." },
  ],
  "Account Change": [
    { name: "user", type: "User", description: "Target account of the change." },
    { name: "actor.user", type: "User", description: "User performing the change." },
    { name: "status_id", type: "integer", description: "Success / Failure of the operation when stated." },
    { name: "activity_id", type: "integer", description: "Create, Enable, Password Change/Reset, Disable, Delete, Lock, MFA…" },
  ],
  "Authentication": [
    { name: "user", type: "User", description: "Authenticated (or attempted) principal." },
    { name: "actor.user", type: "User", description: "Originating actor when distinguishable." },
    { name: "src_endpoint", type: "Endpoint", description: "Client endpoint of the authentication attempt." },
    { name: "dst_endpoint", type: "Endpoint", description: "Server / service targeted by the auth." },
    { name: "status_id", type: "integer", description: "Logon Success / Failure." },
    { name: "activity_id", type: "integer", description: "Logon (1) / Logoff (2) / tickets / preauth." },
  ],
  "User Access Management": [
    { name: "user", type: "User", description: "Account whose privileges were changed." },
    { name: "privileges", type: "Privileges", description: "Privileges granted or revoked when listed." },
    { name: "src_endpoint", type: "Endpoint", description: "Endpoint that initiated the change." },
  ],
  "Network Activity": [
    { name: "src_endpoint", type: "Endpoint", description: "ip, port, hostname of the initiator." },
    { name: "dst_endpoint", type: "Endpoint", description: "ip, port, hostname of the responder." },
    { name: "connection_info", type: "Connection Info", description: "protocol_name / protocol_num / direction when present." },
    { name: "firewall_rule", type: "Firewall Rule", description: "Rule uid / name matching the traffic (from cs1/rule fields)." },
    { name: "app_name", type: "string", description: "Application / service that produced the traffic." },
    { name: "disposition_id", type: "integer", description: "Allowed / Blocked / Dropped decision of the device." },
  ],
  "HTTP Activity": [
    { name: "http_request", type: "HTTP Request", description: "method, user_agent and url (path, hostname, url_string)." },
    { name: "http_response", type: "HTTP Response", description: "HTTP status code." },
    { name: "src_endpoint", type: "Endpoint", description: "Client endpoint (ip / port)." },
    { name: "dst_endpoint", type: "Endpoint", description: "Server endpoint." },
  ],
  "DNS Activity": [
    { name: "query", type: "DNS Query", description: "hostname and query type (A, AAAA, MX…)." },
    { name: "answers", type: "DNS Answer[]", description: "Resolved answer IP(s)." },
    { name: "rcode_id", type: "integer", description: "Response code, e.g. 0 = NOERROR." },
    { name: "connection_info", type: "Connection Info", description: "Transport used for the query (udp/tcp)." },
  ],
  "Application Lifecycle": [
    { name: "app", type: "Application", description: "Application uid / name from the log header." },
    { name: "process", type: "Process", description: "Underlying process when reported." },
    { name: "user", type: "User", description: "User who triggered install / start / stop…" },
    { name: "activity_id", type: "integer", description: "Install, Remove, Start, Stop, Restart, Enable, Disable, Update." },
  ],
  "API Activity": [
    { name: "api", type: "API", description: "operation (/api/v2/events) and service.name (edge-api)." },
    { name: "http_request", type: "HTTP Request", description: "Method, URL of the API call." },
    { name: "http_response", type: "HTTP Response", description: "Response code." },
    { name: "user", type: "User", description: "Caller of the API." },
  ],
  "Datastore Activity": [
    { name: "database", type: "Datastore", description: "database name and type (database name / provider)." },
    { name: "table", type: "Datastore Table", description: "Table being read / written." },
    { name: "query_info", type: "Query Info", description: "The SQL / query text when present." },
    { name: "query", type: "Query", description: "Query level details." },
  ],
};