"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { fmtTime, getJSON, type DlqRecord, type RawEvent } from "../../lib/api";

interface DlqDetail {
  dlq: DlqRecord;
  raw: RawEvent | null;
}

export default function DlqDetailPage() {
  const params = useParams();
  const dlqId = String(params.dlq_id);

  const [detail, setDetail] = useState<DlqDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getJSON<DlqDetail>(`/dlq/${dlqId}`)
      .then(setDetail)
      .catch((e) => setError(String(e)));
  }, [dlqId]);

  if (error) return <main className="main"><div className="error">{error}</div></main>;
  if (!detail) return <main className="main"><div className="loading">Loading…</div></main>;

  const r = detail.dlq;

  return (
    <main className="main">
      <div className="page-head">
        <div>
          <h1>DLQ Record</h1>
          <p className="mono">{r.dlq_id}</p>
        </div>
        <Link href="/dlq" className="btn">← DLQ</Link>
      </div>

      <div className="card">
        <h2>Failure Details</h2>
        <dl className="kv">
          <dt>Raw event ID</dt><dd className="mono">{r.raw_event_id}</dd>
          <dt>Status</dt><dd>{r.status}</dd>
          <dt>Classification</dt><dd>{r.classification ?? "—"}</dd>
          <dt>Parsers attempted</dt><dd className="mono">{r.parsers_attempted?.join(", ") ?? "—"}</dd>
          <dt>First seen</dt><dd>{fmtTime(r.first_seen_at)}</dd>
          <dt>Last attempt</dt><dd>{fmtTime(r.last_attempt_at)}</dd>
          <dt>Reprocess count</dt><dd>{r.reprocess_count ?? 0}</dd>
        </dl>
        {r.metadata && Object.keys(r.metadata).length > 0 && (
          <>
            <div style={{ height: 10 }} />
            <dl className="kv">
              {Object.entries(r.metadata).map(([k, v]) => (
                <div key={k} style={{ display: "contents" }}>
                  <dt>{k}</dt><dd className="mono">{String(v)}</dd>
                </div>
              ))}
            </dl>
          </>
        )}
      </div>

      <div style={{ height: 16 }} />

      <div className="card">
        <h2>Bronze Raw Event</h2>
        {detail.raw ? (
          <>
            <dl className="kv" style={{ marginBottom: 14 }}>
              <dt>Source</dt><dd className="mono">{detail.raw.source_id}</dd>
              <dt>Transport</dt><dd>{detail.raw.transport}</dd>
              <dt>Format hint</dt><dd>{detail.raw.format_hint ?? "—"}</dd>
              <dt>Ingested at</dt><dd>{fmtTime(detail.raw.ingested_at)}</dd>
            </dl>
            <pre className="payload">{detail.raw.raw_payload}</pre>
          </>
        ) : (
          <div className="empty">
            Raw event {r.raw_event_id} not found in Bronze — lineage broken.
          </div>
        )}
      </div>

      <div style={{ height: 16 }} />
      <div className="card">
        <p className="muted" style={{ fontSize: 12.5 }}>
          DLQ reprocessing (replay, parser promotion, rollback) is planned for Module 3.
          No replay actions are exposed yet.
        </p>
      </div>
    </main>
  );
}