"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { fmtTime, getJSON, type NormalizedEvent, type RawEvent } from "../../lib/api";
import { SeverityBadge } from "../../page";

interface EventDetail {
  normalized: NormalizedEvent;
  raw: RawEvent | null;
}

export default function EventDetailPage() {
  const params = useParams();
  const eventId = String(params.event_id);

  const [detail, setDetail] = useState<EventDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getJSON<EventDetail>(`/events/${eventId}`)
      .then(setDetail)
      .catch((e) => setError(String(e)));
  }, [eventId]);

  if (error) return <main className="main"><div className="error">{error}</div></main>;
  if (!detail) return <main className="main"><div className="loading">Loading…</div></main>;

  const n = detail.normalized;

  return (
    <main className="main">
      <div className="page-head">
        <div>
          <h1>Event Detail</h1>
          <p className="mono">{n.event_id}</p>
        </div>
        <Link href="/events" className="btn">← Events</Link>
      </div>

      <div className="trace" style={{ marginBottom: 20 }}>
        <span>Raw log</span> <span className="arrow">→</span>
        <span>{n.parser_id}</span> <span className="arrow">→</span>
        <span>Normalized event</span>
      </div>

      <div className="card">
        <h2>Normalized Event</h2>
        <div className="grid grid-2">
          <dl className="kv">
            <dt>Event ID</dt><dd className="mono">{n.event_id}</dd>
            <dt>Time</dt><dd>{fmtTime(n.time)}</dd>
            <dt>Activity</dt><dd>{n.activity_name ?? "—"}</dd>
            <dt>Category</dt><dd>{n.category_name ?? "—"}</dd>
            <dt>Class</dt><dd>{n.class_name ?? "—"}</dd>
            <dt>Severity</dt><dd><SeverityBadge severity={n.severity} /></dd>
            <dt>User</dt><dd>{n.user ?? "—"}</dd>
            <dt>Action</dt><dd>{n.action ?? "—"}</dd>
          </dl>
          <dl className="kv">
            <dt>Source endpoint</dt><dd className="mono">{n.src_endpoint ?? "—"}</dd>
            <dt>Destination endpoint</dt><dd className="mono">{n.dst_endpoint ?? "—"}</dd>
            <dt>Protocol</dt><dd>{n.protocol_name ?? "—"}</dd>
            <dt>Device</dt><dd>{n.device_id ?? "—"}</dd>
            <dt>App / Service</dt><dd>{n.app_id ?? "—"}</dd>
          </dl>
        </div>
      </div>

      <div style={{ height: 16 }} />

      <div className="card">
        <h2>Traceability</h2>
        <dl className="kv">
          <dt>Raw event ID</dt><dd className="mono">{n.raw_event_id}</dd>
          <dt>Parser ID</dt><dd className="mono">{n.parser_id}</dd>
          <dt>Parser tier</dt><dd>{n.parser_tier}</dd>
          <dt>Confidence</dt><dd>{n.confidence_score?.toFixed(2)}</dd>
          <dt>Schema ID</dt><dd className="mono">{n.schema_id ?? "ulpc-ocsf-v1"}</dd>
          <dt>Schema version</dt><dd className="mono">{n.schema_version ?? "1.0.0"}</dd>
        </dl>
      </div>

      <div style={{ height: 16 }} />

      <div className="card">
        <h2>Bronze Raw Event (lossless original)</h2>
        {detail.raw ? (
          <>
            <dl className="kv" style={{ marginBottom: 14 }}>
              <dt>Raw event ID</dt><dd className="mono">{detail.raw.event_id}</dd>
              <dt>Source</dt><dd className="mono">{detail.raw.source_id}</dd>
              <dt>Transport</dt><dd>{detail.raw.transport}</dd>
              <dt>Format hint</dt><dd>{detail.raw.format_hint ?? "—"}</dd>
              <dt>Collector</dt><dd className="mono">{detail.raw.collector_id}</dd>
              <dt>Ingested at</dt><dd>{fmtTime(detail.raw.ingested_at)}</dd>
              <dt>Upload ID</dt><dd className="mono">{detail.raw.upload_id ?? "—"}</dd>
            </dl>
            <pre className="payload">{detail.raw.raw_payload}</pre>
          </>
        ) : (
          <div className="empty">
            Raw event {n.raw_event_id} not found in Bronze — lineage broken.
          </div>
        )}
      </div>
    </main>
  );
}