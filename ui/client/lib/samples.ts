import type { LogFormat } from "@shared/api";

export const FORMATS: { value: LogFormat | "auto"; label: string }[] = [
  { value: "auto", label: "Auto-detect" },
  { value: "json", label: "JSON" },
  { value: "syslog", label: "Syslog (RFC 5424 / 3164)" },
  { value: "cef", label: "CEF" },
  { value: "leef", label: "LEEF" },
  { value: "keyvalue", label: "Key=Value pairs" },
  { value: "apache", label: "Apache / Nginx access log" },
  { value: "text", label: "Plain text" },
];

export const SAMPLES: { label: string; content: string }[] = [
  {
    label: "Syslog",
    content: `<34>1 2024-01-22T12:42:48Z web1 sshd 2321 - - "Failed password for admin from 203.0.113.5 port 42103 ssh2"
<13>Jan 22 12:43:02 fw01 sshd[1200]: login attempt from 10.1.1.1 user bob succeeded
<86>Jan 22 12:43:09 auth-svc CEF:0|Okta|Identity|1.0|3002|User login|3|rt=1705934589000 suser=carol@corp.com outcome=success login_type=1`,
  },
  {
    label: "CEF",
    content: `CEF:0|Palo Alto Networks|PA-VM|11.0|TRAFFIC|Allow outbound connection|4|rt=Jan 22 2024 12:43:02 src=10.0.0.41 srcPort=51243 dst=10.0.0.12 dpt=443 proto=tcp act=allow cs1Label=rule cs1=allow-web
CEF:0|McAfee|Web Gateway|7.1|102|Blocked site|6|src=203.0.113.9 dst=198.51.100.3 suser=jdoe cat=Web Content act=blocked request="http://evil.example/x"`,
  },
  {
    label: "JSON",
    content: `{"@timestamp":"2024-01-22T12:42:48.000Z","src_ip":"10.0.0.9","dst_ip":"10.0.0.1","msg":"connection reset by peer","proto":"tcp"}
{
  "@timestamp": "2024-01-22T12:42:49.000Z",
  "service": "edge-api",
  "http_request": { "method": "GET", "url": "https://edge.example/api/v2/events" },
  "http_response": { "status": 200 },
  "src_ip": "198.51.100.22"
}`,
  },
  {
    label: "Apache",
    content: `192.168.1.5 - - [22/Jan/2024:12:42:48 +0000] "GET /api/users?id=7 HTTP/1.1" 200 532 "http://site.local/" "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0"
192.168.1.9 - bob [22/Jan/2024:12:43:11 +0000] "POST /api/upload HTTP/1.1" 403 233 "-" "curl/8.5.0"`,
  },
  {
    label: "Key=Value",
    content: `user=bob action=login result=failed src=10.0.0.1 dst=192.168.1.1
query_type=A qname=www.example.com answer=93.184.216.34 dns.rcode=NOERROR
event_type=account_created user=carol outcome=success`,
  },
];

// A single-click bundle that exercises most classes, DLQ and mixed formats.
export const MIXED_SAMPLE = `2024-01-22T12:42:48Z edge-prod INFO  {"@timestamp":"2024-01-22T12:42:48.000Z","service":"edge-api","http_request":{"method":"GET","url":"https://edge.example/api/v2/events?src=weekly"},"http_response":{"status":200},"src_ip":"198.51.100.22"}
<34>1 2024-01-22T12:42:48Z web1 sshd 2321 - - "Failed password for admin from 203.0.113.5 port 42103 ssh2"
<13>Jan 22 12:43:02 fw01 sshd[1200]: login attempt from 10.1.1.1 user bob succeeded
CEF:0|McAfee|Web Gateway|7.1|102|Blocked site|6|src=203.0.113.9 dst=198.51.100.3 suser=jdoe cat=Web Content act=blocked request="http://evil.example/x"
CEF:0|Palo Alto Networks|PA-VM|11.0|TRAFFIC|Allow outbound connection|4|rt=Jan 22 2024 12:43:02 src=10.0.0.41 srcPort=51243 dst=10.0.0.12 dpt=443 proto=tcp act=allow cs1Label=rule cs1=allow-web
192.168.1.5 - - [22/Jan/2024:12:42:48 +0000] "GET /api/users?id=7 HTTP/1.1" 200 532 "http://site.local/" "Mozilla/5.0"
query_type=A qname=www.example.com answer=93.184.216.34 dns.rcode=NOERROR
user=bob action=login result=failed src=10.0.0.1 dst=192.168.1.1
event_type=account_created user=carol outcome=success
The quick brown fox jumps over the lazy dog
==============================`;