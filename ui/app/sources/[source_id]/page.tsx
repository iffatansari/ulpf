"use client";

import { useEffect, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import {
  fmtNumber,
  fmtTime,
  getJSON,
  uploadFile,
  type NormalizedEvent,
  type Source,
  type SourceStats,
  type UploadJob,
} from "../../lib/api";
import { SeverityBadge, StatusBadge } from "../../page";

interface UploadResponse {
  upload_id: string;
  source_id: string;
  filename: string;
  status: string;
}

export default function SourceDetailPage() {
  const params = useParams();
  const sourceId = String(params.source_id);
  const router = useRouter();

  const [source, setSource] = useState<Source | null>(null);
  const [stats, setStats] = useState<SourceStats | null>(null);
  const [events, setEvents] = useState<NormalizedEvent[]>([]);
  const [uploads, setUploads] = useState<UploadJob[]>([]);
  const [error, setError] = useState<string | null>(null);

  const [file, setFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);

  const fileInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    Promise.all([
      getJSON<{ source: Source }>(`/sources/${sourceId}`),
      getJSON<SourceStats>(`/sources/${sourceId}/stats`),
      getJSON<{ events: NormalizedEvent[] }>(`/sources/${sourceId}/events?limit=10`),
      getJSON<{ uploads: UploadJob[] }>(`/sources/${sourceId}/uploads`),
    ])
      .then(([s, st, ev, up]) => {
        setSource(s.source);
        setStats(st);
        setEvents(ev.events);
        setUploads(up.uploads);
      })
      .catch((e) => setError(String(e)));
  }, [sourceId]);

  async function doUpload() {
    if (!file) return;
    setUploading(true);
    setUploadError(null);
    try {
      const res = await uploadFile<UploadResponse>(
        `/sources/${sourceId}/upload`,
        file
      );
      setFile(null);
      if (fileInput.current) fileInput.current.value = "";
      router.push(`/uploads/${res.upload_id}`);
    } catch (e) {
      setUploadError(String(e));
    } finally {
      setUploading(false);
    }
  }

  if (error) return <main className="main"><div className="error">{error}</div></main>;
  if (!source) return <main className="main"><div className="loading">Loading…</div></main>;

  const statRows: Array<[string, number, string?]> = [
    ["Raw events (Bronze)", stats?.raw_events ?? 0],
    ["Normalized (Silver)", stats?.normalized_events ?? 0],
    ["DLQ", stats?.dlq_events ?? 0],
    ["Success rate", stats?.success_rate ?? 0, "%"],
  ];

  return (
    <main className="main">
      <div className="page-head">
        <div>
          <h1>{source.name}</h1>
          <p className="mono">{source.source_id}</p>
        </div>
        <Link href="/sources" className="btn">← Sources</Link>
      </div>

      <div className="grid grid-2">
        <div className="card">
          <h2>Source Information</h2>
          <dl className="kv">
            <dt>Source type</dt><dd>{source.source_type}</dd>
            <dt>Transport</dt><dd>{source.transport}</dd>
            <dt>Expected format</dt><dd>{source.expected_format}</dd>
            <dt>Enabled</dt>
            <dd><span className={`badge ${source.enabled ? "badge-green" : "badge-gray"}`}>{source.enabled ? "enabled" : "disabled"}</span></dd>
            <dt>Created</dt><dd>{fmtTime(source.created_at)}</dd>
            <dt>Last upload</dt><dd>{fmtTime(source.last_upload_at)}</dd>
            <dt>Last seen</dt><dd>{fmtTime(source.last_seen_at)}</dd>
            {source.description && (<><dt>Description</dt><dd>{source.description}</dd></>)}
          </dl>
        </div>

        <div className="card">
          <h2>Upload Log File</h2>
          <input
            ref={fileInput}
            type="file"
            style={{ marginBottom: 10 }}
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          />
          <button
            className="btn btn-primary"
            disabled={!file || uploading}
            onClick={doUpload}
          >
            {uploading ? "Uploading…" : file ? `Upload ${file.name}` : "Select a file"}
          </button>
          {uploadError && <div className="error" style={{ marginTop: 12 }}>{uploadError}</div>}
          <p className="form-note" style={{ marginTop: 12 }}>
            The file is streamed line by line into logs.raw and processed through the
            existing orchestrator. Multiple formats in one file are supported.
          </p>
        </div>
      </div>

      <div style={{ height: 16 }} />

      <div className="card">
        <h2>Processing Statistics</h2>
        <div className="grid grid-4">
          {statRows.map(([label, value, suffix]) => (
            <div className="stat" key={label}>
              <span className="stat-label">{label}</span>
              <span className="stat-value">{fmtNumber(value)}{suffix}</span>
            </div>
          ))}
        </div>
        <div style={{ height: 14 }} />
        <dl className="kv">
          <dt>Last ingested</dt><dd>{stats?.last_ingested_at ? fmtTime(stats.last_ingested_at) : "—"}</dd>
          <dt>Blank lines skipped</dt><dd>{fmtNumber(stats?.blank_lines)}</dd>
          <dt>Line errors</dt><dd>{fmtNumber(stats?.line_errors)}</dd>
        </dl>
        {stats && stats.formats && Object.keys(stats.formats).length > 0 && (
          <>
            <div style={{ height: 14 }} />
            <h2 style={{ fontSize: 13 }}>Format Distribution</h2>
            <div className="kv" style={{ gridTemplateColumns: "120px 1fr" }}>
              {Object.entries(stats.formats).map(([fmt, n]) => (
                <div key={fmt} style={{ display: "contents" }}>
                  <dt>{fmt}</dt>
                  <dd>{fmtNumber(n)}</dd>
                </div>
              ))}
            </div>
          </>
        )}
      </div>

      <div style={{ height: 16 }} />

      <div className="grid grid-2">
        <div className="card">
          <h2>Recent Uploads</h2>
          {uploads.length === 0 ? (
            <div className="empty">No uploads for this source yet.</div>
          ) : (
            <div className="table-wrap">
              <table className="tbl">
                <thead>
                  <tr>
                    <th>File</th>
                    <th>Status</th>
                    <th>Lines</th>
                    <th>Created</th>
                  </tr>
                </thead>
                <tbody>
                  {uploads.map((u) => (
                    <tr key={u.upload_id}>
                      <td><Link href={`/uploads/${u.upload_id}`} className="mono">{u.filename}</Link></td>
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

        <div className="card">
          <h2>Recent Events</h2>
          {events.length === 0 ? (
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
                  </tr>
                </thead>
                <tbody>
                  {events.map((e) => (
                    <tr key={e.event_id}>
                      <td className="mono">{fmtTime(e.time)}</td>
                      <td><Link href={`/events/${e.event_id}`}>{e.activity_name ?? "—"}</Link></td>
                      <td><SeverityBadge severity={e.severity} /></td>
                      <td>{e.user ?? "—"}</td>
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