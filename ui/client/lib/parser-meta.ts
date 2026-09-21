export interface ParserMeta {
  id: string;
  name: string;
  formats: string[];
  priority: string;
  description: string;
  features: string[];
  example: string;
}

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
    features: ["RFC 5424 structured data parsed and flattened", "PRI severity mapped to OCSF (0-7 level table)", "APP-NAME→process name, PROCID→app_name fallback"],
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
    id: "leef",
    name: "LEEF",
    formats: ["leef"],
    priority: "4",
    description: "IBM LEEF 2.0: LEEF:Version|Vendor|Product|Version|EventID|Delimiter|Key|Key=Value pairs.",
    features: ["Custom delimiter support", "Vendor / product / event id preserved", "Key=Value extension parsed like CEF"],
    example: 'LEEF:1.0|IBM|QRadar|7.5|2002|\\t|src=10.0.0.1 sev=6 devTime=Jan 22 2024 12:43:02',
  },
  {
    id: "keyvalue",
    name: "Key=Value",
    formats: ["keyvalue"],
    priority: "5",
    description: "Space separated name=value pairs — the lingua franca produced by iptables, cloudtrail-ish and custom middleware.",
    features: ["Requires >= 3 balanced pairs to avoid prose", "Quoted values may contain spaces", "Alias table maps dpt→dst port, suser→user, act→action …"],
    example: "user=bob action=login result=failed src=10.0.0.1 dst=192.168.1.1",
  },
  {
    id: "apache",
    name: "Apache / Nginx",
    formats: ["apache"],
    priority: "6",
    description: "Combined access log format (CLF with referer + user agent). IP, ident and authuser before the timestamp bracket.",
    features: ["Method, path and status extracted", "Response bytes / referer / user_agent kept", "Screen height parsing for virtual hosts"],
    example: '192.168.1.5 - - [22/Jan/2024:12:42:48 +0000] "GET /api/users?id=7 HTTP/1.1" 200 532 "http://site.local/" "Mozilla/5.0"',
  },
  {
    id: "text",
    name: "Plain Text",
    formats: ["text"],
    priority: "7 — last resort",
    description: "Fallback for human-readable app logs. Only familiar signal phrases (login failed, started, attack, blocked…) are accepted — pure prose is rejected.",
    features: ["Signal-based heuristic, no fabrication", "Time prefixes parsed when present", "Rejects filler lines with a clear reason"],
    example: "2024-01-22T12:42:48Z api-gateway login for user bob failed: bad password",
  },
];