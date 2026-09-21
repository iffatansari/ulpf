"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import {
  fmtNumber,
  fmtTime,
  getJSON,
  type NormalizedEvent,
  type UploadJob,
} from "./lib/api";

interface DashboardData {
  sources: number;
  normalized_events: number;
  dlq_events: number;
  uploads: number;
  recent_events: NormalizedEvent[];
  recent_uploads: UploadJob[];
}

export default function Dashboard() {
  const [data, setData] = useState<DashboardData | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getJSON<DashboardData>("/dashboard")
      .then(setData)
      .catch((e) => setError(String(e)));
  }, []);

  if (error) return <main className="main"><div className="error">{error}</div></main>;
  if (!data) return <main className="main"><div className="loading">Loading…</div></main>;

  return (
    <main className="main">
      <div className="page-head">
        <div>
          <h1>Dashboard</h1>
          <p>Operational overview of the ULPF pipeline.</p>
        </div>
        <Link href="/sources/new" className="btn btn-primary">+ Add Log Source</Link>
      </div>

      <div className="grid grid-4">
        <div className="card stat">
          <span className="stat-label">Sources</span>
          <span className="stat-value">{fmtNumber(data.sources)}</span>
          <Link href="/sources" className="stat-sub">View source registry →</Link>
        </div>
        <div className="card stat">
          <span className="stat-label">Normalized Events</span>
          <span className="stat-value" style={{ color: "var(--green)" }}>
            {fmtNumber(data.normalized_events)}
          </span>
          <Link href="/events" className="stat-sub">View silver events →</Link>
        </div>
        <div className="card stat">
          <span className="stat-label">DLQ Events</span>
          <span className="stat-value" style={{ color: "var(--yellow)" }}>
            {fmtNumber(data.dlq_events)}
          </span>
          <Link href="/dlq" className="stat-sub">View failures →</Link>
        </div>
        <div className="card stat">
          <span className="stat-label">Uploads</span>
          <span className="stat-value">{fmtNumber(data.uploads)}</span>
          <Link href="/sources" className="stat-sub">Upload a log file →</Link>
        </div>
      </div>

      <div style={{ height: 20 }} />

      <div className="grid grid-2">
        <div className="card">
          <h2>Recent Events</h2>
          {data.recent_events.length === 0 ? (
            <div className="empty">No normalized events yet.</div>
          ) : (
            <div className="table-wrap">
              <table className="tbl">
                <thead>
                  <tr>
                    <th>Time</th>
                    <th>Activity</th>
                    <th>Severity</th>
                    <th>User</th>
                    <th>Parser</th>
                  </tr>
                </thead>
                <tbody>
                  {data.recent_events.map((e) => (
                    <tr key={e.event_id}>
                      <td className="mono">{fmtTime(e.time)}</td>
                      <td>
                        <Link href={`/events/${e.event_id}`}>
                          {e.activity_name ?? "—"}
                        </Link>
                      </td>
                      <td><SeverityBadge severity={e.severity} /></td>
                      <td>{e.user ?? "—"}</td>
                      <td className="mono">{e.parser_id}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        <div className="card">
          <h2>Recent Uploads</h2>
          {data.recent_uploads.length === 0 ? (
            <div className="empty">No uploads yet.</div>
          ) : (
            <div className="table-wrap">
              <table className="tbl">
                <thead>
                  <tr>
                    <th>File</th>
                    <th>Source</th>
                    <th>Status</th>
                    <th>Lines</th>
                    <th>Uploaded</th>
                  </tr>
                </thead>
                <tbody>
                  {data.recent_uploads.map((u) => (
                    <tr key={u.upload_id}>
                      <td>
                        <Link href={`/uploads/${u.upload_id}`} className="mono">
                          {u.filename}
                        </Link>
                      </td>
                      <td className="mono">{u.source_id}</td>
                      <td><StatusBadge status={u.status} /></td>
                      <td>{fmtNumber(u.total_lines)}</td>
                      <td>{fmtTime(u.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </main>
  );
}

export function SeverityBadge({ severity }: { severity?: string | null }) {
  if (!severity) return <span className="badge badge-gray">—</span>;
  const cls =
    severity === "critical" || severity === "high"
      ? "badge-red"
      : severity === "medium"
        ? "badge-yellow"
        : "badge-green";
  return <span className={`badge ${cls}`}>{severity}</span>;
}

export function StatusBadge({ status }: { status: string }) {
  const cls =
    status === "completed"
      ? "badge-green"
      : status === "processing"
        ? "badge-yellow"
        : status === "failed"
          ? "badge-red"
          : "badge-gray";
  return <span className={`badge ${cls}`}>{status}</span>;
}