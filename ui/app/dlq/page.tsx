"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { fmtTime, getJSON, type DlqRecord } from "../lib/api";

export default function DLQPage() {
  const [records, setRecords] = useState<DlqRecord[]>([]);
  const [total, setTotal] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getJSON<{ total: number; records: DlqRecord[] }>("/dlq")
      .then((d) => {
        setRecords(d.records);
        setTotal(d.total);
      })
      .catch((e) => setError(String(e)));
  }, []);

  return (
    <main className="main">
      <div className="page-head">
        <div>
          <h1>Dead Letter Queue</h1>
          <p>
            Events that could not be normalized. Inspect-only — reprocessing becomes
            available in Module 3.
          </p>
        </div>
      </div>

      {error && <div className="error">{error}</div>}
      <p className="muted" style={{ marginBottom: 10 }}>
        {total === null ? "…" : total.toLocaleString()} record(s)
      </p>

      <div className="table-wrap">
        <table className="tbl">
          <thead>
            <tr>
              <th>Raw Event ID</th>
              <th>Status</th>
              <th>Classification</th>
              <th>Parsers Attempted</th>
              <th>First Seen</th>
              <th>Last Attempt</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {records.length === 0 && (
              <tr><td colSpan={7} className="empty">No DLQ records. All events normalized successfully.</td></tr>
            )}
            {records.map((r) => (
              <tr key={r.dlq_id}>
                <td className="mono">{r.raw_event_id}</td>
                <td>
                  <span className={`badge ${r.status === "unknown" ? "badge-yellow" : "badge-red"}`}>
                    {r.status}
                  </span>
                </td>
                <td>{r.classification ?? "—"}</td>
                <td className="mono">
                  {r.parsers_attempted?.length
                    ? r.parsers_attempted.join(", ")
                    : "—"}
                </td>
                <td>{fmtTime(r.first_seen_at)}</td>
                <td>{fmtTime(r.last_attempt_at)}</td>
                <td>
                  <Link href={`/dlq/${r.dlq_id}`} className="badge badge-info">
                    inspect →
                  </Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </main>
  );
}