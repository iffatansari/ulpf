import { describe, it, expect } from "vitest";
import { normalizeLogInput } from "./index";
import { LineResult, LogFormat } from "../../shared/api";

function normalize(content: string, format: LogFormat | "auto" = "auto") {
  return normalizeLogInput({ content, source: { format } });
}

function ok(r: LineResult) {
  return r.ok ? r : null;
}

describe("normalizeLogInput: format detection and conversion", () => {
  it("parses RFC 5424 syslog into base/system event", () => {
    const res = normalize('<34>1 2024-01-22T12:42:48Z web1 sshd 2321 - - "Failed password for admin from 203.0.113.5 port 42103 ssh2"');
    expect(res.summary.events).toBe(1);
    expect(res.summary.rejected).toBe(0);
    const r = ok(res.lines[0]);
    expect(r).not.toBeNull();
    const ev = r!.event;
    expect(ev.class_name).toBe("Authentication");
    expect(ev.activity_id).toBe(1); // logon
    expect(ev.severity_id).toBe(5); // syslog severity 2 -> critical
    expect(ev.time).toBe(Date.parse("2024-01-22T12:42:48Z"));
    expect(ev.device?.hostname).toBe("web1");
    expect(ev.message).toContain("Failed password");
  });

  it("parses RFC 3164 syslog with facility severity", () => {
    const res = normalize('<13>Jan 22 12:43:02 firewall sshd[1200]: login attempt from 10.1.1.1 user bob succeeded');
    expect(res.summary.events).toBe(1);
    const r = ok(res.lines[0]);
    const ev = r!.event;
    expect(ev.severity_id).toBe(2); // syslog severity 5 -> low
    expect(ev.message).toContain("login attempt");
  });

  it("classifies key=value auth line as Authentication", () => {
    const res = normalize('user=bob action=login result=failed src=10.0.0.1 dst=192.168.1.1');
    expect(res.summary.events).toBe(1);
    const r = ok(res.lines[0]);
    const ev = r!.event;
    expect(ev.class_name).toBe("Authentication");
    expect(ev.activity_id).toBe(1); // logon
    expect(ev.user?.name).toBe("bob");
    expect(ev.status_id).toBe(2); // result=failed
    expect(ev.src_endpoint?.ip).toBe("10.0.0.1");
    expect(ev.dst_endpoint?.ip).toBe("192.168.1.1");
  });

  it("classifies CEF header + extension", () => {
    const res = normalize(
      'CEF:0|McAfee|Web Gateway|7.1|102|Blocked site|6|src=203.0.113.9 dst=198.51.100.3 suser=jdoe cat=Web Content act=blocked request="http://evil.example/x" cs1Label=policy cs1=default',
    );
    expect(res.summary.events).toBe(1);
    const r = ok(res.lines[0]);
    const ev = r!.event;
    expect(ev.class_name).toBe("Network Activity");
    expect(ev.activity_id).toBe(5); // refused (blocked)
    expect(ev.metadata.log?.product).toBe("Web Gateway");
    expect(ev.metadata.log?.provider).toBe("McAfee");
    expect(ev.src_endpoint?.ip).toBe("203.0.113.9");
    expect(ev.dst_endpoint?.ip).toBe("198.51.100.3");
    expect(ev.user?.name).toBe("jdoe");
    expect(ev.severity_id).toBe(3); // CEF 6 -> medium
    expect(ev.disposition_id).toBe(2); // blocked
    expect(ev.action_id).toBe(2); // denied
    expect(ev.raw_data).toContain("CEF:0|");
  });

  it("classifies Apache/Nginx access log as HTTP Activity", () => {
    const res = normalize(
      '192.168.1.5 - - [22/Jan/2024:12:42:48 +0000] "GET /api/users?id=7 HTTP/1.1" 200 532 "http://site.local/" "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0"',
    );
    expect(res.summary.events).toBe(1);
    const r = ok(res.lines[0]);
    const ev = r!.event;
    expect(ev.class_name).toBe("HTTP Activity");
    expect(ev.activity_id).toBe(3); // GET
    expect(ev.http_response?.code).toBe(200);
    expect(ev.status_id).toBe(1);
    expect(ev.http_request?.method).toBe("GET");
    expect(ev.http_request?.url?.url_string).toContain("/api/users?id=7");
    expect(ev.src_endpoint?.ip).toBe("192.168.1.5");
  });

  it("classifies DNS log lines", () => {
    const res = normalize('query_type=A qname=www.example.com answer=93.184.216.34 dns.rcode=NOERROR');
    expect(res.summary.events).toBe(1);
    const r = ok(res.lines[0]);
    const ev = r!.event;
    expect(ev.class_name).toBe("DNS Activity");
    expect(ev.rcode_id).toBe(0);
    expect(ev.query?.hostname).toBe("www.example.com");
    expect(ev.answers?.[0]?.ip).toBe("93.184.216.34");
  });

  it("emits one event per JSON line and preserves values", () => {
    const res = normalize('{"@timestamp":"2024-01-22T12:42:48.000Z","src_ip":"10.0.0.9","dst_ip":"10.0.0.1","msg":"connection reset by peer"}');
    expect(res.summary.events).toBe(1);
    const r = ok(res.lines[0]);
    const ev = r!.event;
    expect(ev.class_name).toBe("Network Activity");
    expect(ev.src_endpoint?.ip).toBe("10.0.0.9");
    expect(ev.time).toBe(Date.parse("2024-01-22T12:42:48.000Z"));
  });

  it("treats pretty-printed multi-line JSON as one logical line", () => {
    const content = '{\n  "@timestamp": "2024-01-22T12:42:48.000Z",\n  "event_type": "create_user",\n  "user": "alice"\n}';
    const res = normalize(content);
    expect(res.summary.total_lines).toBe(1);
    expect(res.summary.events).toBe(1);
    const r = ok(res.lines[0]);
    expect(r!.event.user?.name).toBe("alice");
  });

  it("expands a JSON array into multiple events", () => {
    const content = '[{"src_ip":"10.0.0.1","src_port":1234},{"src_ip":"10.0.0.2","src_port":5678}]';
    const res = normalize(content);
    expect(res.summary.events).toBe(2);
  });
});

describe("normalizeLogInput: multi-parser fallback chain", () => {
  it("rescues a line via the generic parser when the primary parser fails", () => {
    const res = normalize("user=bob action=login result=failed src=10.0.0.1 dst=192.168.1.1", "json");
    expect(res.summary.events).toBe(1);
    expect(res.summary.rescued).toBe(1);
    const r = res.lines[0];
    expect(ok(r)?.parser_chain).toEqual(["JSON Parser", "Key/Value Parser"]);
    expect(ok(r)?.chain_rescued).toBe(true);
    expect(ok(r)?.event.class_name).toBe("Authentication");
  });

  it("records every parser attempted on a rejected line", () => {
    const res = normalize("definitely not a log at all", "cef");
    expect(res.summary.events).toBe(0);
    expect(res.summary.rejected).toBe(1);
    const r = res.lines[0] as Extract<LineResult, { ok: false }>;
    expect(r.tried_parsers).toEqual(["CEF Parser", "Key/Value Parser", "Text Parser"]);
  });

  it("keeps clean formats on the primary parser without a rescue flag", () => {
    const res = normalize('192.168.0.5 - alice [22/Jan/2024:12:42:48 +0000] "GET /health HTTP/1.1" 200 512 "-" "curl/8"');
    expect(res.summary.events).toBe(1);
    expect(res.summary.rescued).toBe(0);
    expect(ok(res.lines[0])?.parser_chain).toEqual(["HTTP Access Log Parser"]);
    expect(ok(res.lines[0])?.chain_rescued).toBe(false);
  });

  it("never rescues pure noise through the fallback parser", () => {
    const res = normalize("---\n====\n###\n", "json");
    expect(res.summary.events).toBe(0);
    expect(res.summary.rejected).toBe(3);
    expect(res.lines.every((l) => !l.ok)).toBe(true);
  });
});

describe("normalizeLogInput: rejection and noise filtering", () => {
  it("rejects empty and separator noise lines", () => {
    const res = normalize("\n---\n====\n###\n", "text");
    expect(res.summary.events).toBe(0);
    expect(res.summary.rejected).toBe(3);
    expect(res.lines.every((l) => !l.ok)).toBe(true);
  });

  it("rejects prose without log vocabulary", () => {
    const res = normalize("The quick brown fox jumps over the lazy dog", "text");
    expect(res.summary.events).toBe(0);
    expect(res.summary.rejected).toBe(1);
  });

  it("rejects non-matching forced parse", () => {
    const res = normalize("not a log at all nonsense words", "json");
    expect(res.summary.events).toBe(0);
    expect(res.lines[0].ok).toBe(false);
  });

  it("accepts a bare log phrase when it carries log vocabulary", () => {
    const res = normalize("sshd: authentication failed for root", "text");
    expect(res.summary.events).toBe(1);
  });
});