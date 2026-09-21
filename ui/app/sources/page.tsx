"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { fmtTime, getJSON, type Source } from "../lib/api";

export default function SourcesPage() {
  const [sources, setSources] = useState<Source[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getJSON<{ sources: Source[] }>("/sources")
      .then((d) => setSources(d.sources))
      .catch((e) => setError(String(e)));
  }, []);

  return (
    <main className="main">
      <div className="page-head">
        <div>
          <h1>Log Sources</h1>
          <p>Registered sources in the Source Registry (ulpf-sources).</p>
        </div>
        <Link href="/sources/new" className="btn btn-primary">+ Add Log Source</Link>
      </div>

      {error && <div className="error">{error}</div>}

      <div className="table-wrap">
        <table className="tbl">
          <thead>
            <tr>
              <th>Name</th>
              <th>Source ID</th>
              <th>Type</th>
              <th>Transport</th>
              <th>Expected Format</th>
              <th>Enabled</th>
              <th>Last Activity</th>
              <th>Created</th>
            </tr>
          </thead>
          <tbody>
            {sources.length === 0 && (
              <tr>
                <td colSpan={8} className="empty">
                  No sources yet. <Link href="/sources/new">Add your first source →</Link>
                </td>
              </tr>
            )}
            {sources.map((s) => (
              <tr key={s.source_id}>
                <td>
                  <Link href={`/sources/${s.source_id}`}>{s.name}</Link>
                </td>
                <td className="mono">{s.source_id}</td>
                <td>{s.source_type}</td>
                <td><span className="badge badge-info">{s.transport}</span></td>
                <td>{s.expected_format}</td>
                <td>
                  <span className={`badge ${s.enabled ? "badge-green" : "badge-gray"}`}>
                    {s.enabled ? "enabled" : "disabled"}
                  </span>
                </td>
                <td>{fmtTime(s.last_seen_at)}</td>
                <td>{fmtTime(s.created_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </main>
  );
}