"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { fmtTime, getJSON, type NormalizedEvent } from "../lib/api";
import { SeverityBadge } from "../page";

export default function EventsPage() {
  const [events, setEvents] = useState<NormalizedEvent[]>([]);
  const [total, setTotal] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [sourceId, setSourceId] = useState("");
  const [severity, setSeverity] = useState("");
  const [parserId, setParserId] = useState("");
  const [limit, setLimit] = useState(100);

  useEffect(() => {
    let alive = true;

    const fetchEvents = () => {
      const qs = new URLSearchParams();
      qs.set("limit", String(limit));
      if (sourceId) qs.set("source_id", sourceId);
      if (severity) qs.set("severity", severity);
      if (parserId) qs.set("parser_id", parserId);
      getJSON<{ total: number; events: NormalizedEvent[] }>(
        `/events?${qs.toString()}`
      )
        .then((res) => {
          if (!alive) return;
          setEvents(res.events);
          setTotal(res.total);
          setError(null);
        })
        .catch((e) => {
          if (alive) setError(String(e));
        });
    };

    fetchEvents();
    const t = setInterval(fetchEvents, 4000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [sourceId, severity, parserId, limit]);

  return (
    <main className="main">
      <div className="page-head">
        <div>
          <h1>Unified Events</h1>
          <p>Normalized Silver events. Different raw formats share one common schema.</p>
        </div>
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <div className="grid grid-4">
          <label className="field" style={{ marginBottom: 0 }}>
            <span>Source ID</span>
            <input type="text" value={sourceId} onChange={(e) => setSourceId(e.target.value)} placeholder="any" />
          </label>
          <label className="field" style={{ marginBottom: 0 }}>
            <span>Severity</span>
            <select value={severity} onChange={(e) => setSeverity(e.target.value)}>
              <option value="">any</option>
              <option value="low">low</option>
              <option value="medium">medium</option>
              <option value="high">high</option>
              <option value="critical">critical</option>
            </select>
          </label>
          <label className="field" style={{ marginBottom: 0 }}>
            <span>Parser</span>
            <select value={parserId} onChange={(e) => setParserId(e.target.value)}>
              <option value="">any</option>
              <option value="syslog-parser-v1">syslog-parser-v1</option>
              <option value="json-parser-v1">json-parser-v1</option>
              <option value="cef-parser-v1">cef-parser-v1</option>
            </select>
          </label>
          <label className="field" style={{ marginBottom: 0 }}>
            <span>Limit</span>
            <select value={limit} onChange={(e) => setLimit(Number(e.target.value))}>
              <option value={50}>50</option>
              <option value={100}>100</option>
              <option value={200}>200</option>
              <option value={500}>500</option>
            </select>
          </label>
        </div>
      </div>

      {error && <div className="error">{error}</div>}
      <p className="muted" style={{ marginBottom: 10 }}>
        {total === null ? "…" : fmtTimeOrNumber(total)} event(s)
      </p>

      <div className="table-wrap">
        <table className="tbl">
          <thead>
            <tr>
              <th>Time</th>
              <th>Source</th>
              <th>Activity</th>
              <th>Severity</th>
              <th>User</th>
              <th>Src Endpoint</th>
              <th>Dst Endpoint</th>
              <th>Action</th>
              <th>Parser</th>
              <th>Conf.</th>
            </tr>
          </thead>
          <tbody>
            {events.length === 0 && (
              <tr><td colSpan={10} className="empty">No events match.</td></tr>
            )}
            {events.map((e) => (
              <tr key={e.event_id}>
                <td className="mono">{fmtTime(e.time)}</td>
                <td className="mono">
                  {typeof e.extensions?.source_id === "string"
                    ? (e.extensions.source_id as string)
                    : "—"}
                </td>
                <td>
                  <Link href={`/events/${e.event_id}`}>{e.activity_name ?? "—"}</Link>
                </td>
                <td><SeverityBadge severity={e.severity} /></td>
                <td>{e.user ?? "—"}</td>
                <td className="mono">{e.src_endpoint ?? "—"}</td>
                <td className="mono">{e.dst_endpoint ?? "—"}</td>
                <td>{e.action ?? "—"}</td>
                <td className="mono">{e.parser_id}</td>
                <td>{e.confidence_score?.toFixed(2) ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </main>
  );
}

function fmtTimeOrNumber(v: number): string {
  return v.toLocaleString();
}