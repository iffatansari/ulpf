export interface ParserMeta {
  id: string;
  name: string;
  formats: string[];
  priority: string;
  description: string;
  features: string[];
  example: string;
}

/**
 * These are the only parser ids the orchestrator actually runs. The order and
 * the existence of each entry are enforced by the backend registry
 * (orchestrator/parsers/registry.py) and its anti-drift test -- this file is
 * documentation copy, not the list of what exists. Never add an entry here
 * without a matching real parser in orchestrator/main.py.
 */
export const PARSERS: ParserMeta[] = [
  {
    id: "json",
    name: "JSON Lines",
    formats: ["json"],
    priority: "1 — first attempt",
    description: "One object per line, or coalesced pretty-printed objects. Field names map to OCSF attributes via alias tables.",
    features: ["Balanced-brace coalescing of multi-line objects", "(timestamp|src_ip|user) style aliases", "Unknown keys preserved under unmapped"],
    example: '{"@timestamp":"2024-01-22T12:42:48Z","src_ip":"10.0.0.9","msg":"connection reset"}',
  },
  {
    id: "syslog",
    name: "Syslog",
    formats: ["syslog"],
    priority: "2",
    description: "Supports RFC 5424 (<PRI>1 TIMESTAMP HOST APP PID MSGID SD MSG) and legacy RFC 3164 (<PRI>Mmm dd hh:mm:ss host app[pid]: msg).",
    features: ["RFC 5424 structured data parsed and flattened, quote-aware", "NILVALUE handled, MSGID and PROCID captured", "RFC 3164 timestamps get an explicit year in UTC"],
    example: '<34>1 2024-01-22T12:42:48Z web1 sshd 2321 - - "Failed password for admin"',
  },
  {
    id: "cef",
    name: "CEF",
    formats: ["cef"],
    priority: "3",
    description: "ArcSight CEF: header CEF:Version|Vendor|Product|Version|Signature|Severity|Name plus a key=value extension.",
    features: ["Vendor / product header → metadata.log", "Name → event name, CEF severity 0-10 mapped", "Extension keys parsed with quoted-value support"],
    example: 'CEF:0|Palo Alto Networks|PA-VM|11.0|TRAFFIC|Allow outbound connection|4|src=10.0.0.41 srcPort=51243 dst=10.0.0.12 dpt=443 act=allow',
  },
  {
    id: "drain3-fallback-v1",
    name: "Drain3 fallback",
    formats: ["text"],
    priority: "4 — last resort",
    description: "Drain3 clusters log lines by template so variable parts (ids, paths, IPs) become fields. Accepts any line as a template, so it will claim lines the parsers above reject.",
    features: ["Template-based clustering via the drain3 library", "Unmatched lines still yield a template cluster", "Runs last, so a claimed line is a false negative for the parser that should have taken it"],
    example: "2024-01-22T12:42:48Z api-gateway login for user bob failed: bad password",
  },
];