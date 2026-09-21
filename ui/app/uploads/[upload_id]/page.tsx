"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { fmtNumber, fmtTime, getJSON, type UploadJob } from "../../lib/api";
import { StatusBadge } from "../../page";

const TERMINAL: Record<string, string> = { completed: "success", failed: "error" } as const;
const POLL_MS = 2000;

export default function UploadPage() {
  const params = useParams();
  const uploadId = String(params.upload_id);

  const [upload, setUpload] = useState<UploadJob | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;

    async function poll() {
      try {
        const d = await getJSON<{ upload: UploadJob }>(`/uploads/${uploadId}`);
        if (cancelled) return;
        setUpload(d.upload);
        // The collector finishes publishing before the orchestrator has
        // persisted everything. Only stop polling once the raw Bronze
        // count has caught up with the number of published events.
        const settled =
          d.upload.status === "failed" ||
          (d.upload.status === "completed" &&
            (d.upload.raw_events ?? 0) >= (d.upload.published_events ?? 0));
        if (settled) {
          setDone(true);
          return;
        }
      } catch (e) {
        if (!cancelled) setError(String(e));
        return;
      }
      timer = setTimeout(poll, POLL_MS);
    }

    poll();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [uploadId]);

  if (error) return <main className="main"><div className="error">{error}</div></main>;
  if (!upload) return <main className="main"><div className="loading">Loading…</div></main>;

  const counters: Array<[string, number]> = [
    ["Total lines read", upload.total_lines ?? 0],
    ["Events published", upload.published_events ?? 0],
    ["Blank lines skipped", upload.blank_lines ?? 0],
    ["Line errors", upload.line_errors ?? 0],
    ["Raw events (Bronze)", upload.raw_events ?? upload.published_events ?? 0],
    ["Normalized (Silver)", upload.normalized_events ?? 0],
    ["DLQ", upload.dlq_events ?? 0],
  ];

  return (
    <main className="main">
      <div className="page-head">
        <div>
          <h1>Upload Processing</h1>
          <p className="mono">{upload.filename} · {upload.upload_id}</p>
        </div>
        <Link href={`/sources/${upload.source_id}`} className="btn">← Source</Link>
      </div>

      <div className="grid grid-4">
        {counters.map(([label, value]) => (
          <div className="card stat" key={label}>
            <span className="stat-label">{label}</span>
            <span className="stat-value">{fmtNumber(value)}</span>
          </div>
        ))}
        <div className="card stat">
          <span className="stat-label">Status</span>
          <span className="stat-value">
            <StatusBadge status={upload.status} />
          </span>
          <span className="stat-sub">
            {done
              ? upload.status === "completed"
                ? "Processing finished at " + fmtTime(upload.completed_at)
                : "Upload failed"
              : TERMINAL[upload.status]
                ? ""
                : "Polling every few seconds…"}
          </span>
        </div>
      </div>

      {upload.status === "failed" && upload.failure_reason && (
        <div className="error" style={{ marginTop: 16 }}>Failure reason: {upload.failure_reason}</div>
      )}

      <div style={{ height: 16 }} />
      <div className="card">
        <h2>Timeline</h2>
        <dl className="kv">
          <dt>Created</dt><dd>{fmtTime(upload.created_at)}</dd>
          <dt>Started processing</dt><dd>{upload.started_at ? fmtTime(upload.started_at) : "—"}</dd>
          <dt>Completed</dt><dd>{upload.completed_at ? fmtTime(upload.completed_at) : "—"}</dd>
        </dl>
      </div>
    </main>
  );
}