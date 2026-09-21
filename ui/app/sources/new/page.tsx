"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import {
  EXPECTED_FORMATS,
  SOURCE_TYPES,
  TRANSPORTS,
  postJSON,
} from "../../lib/api";

export default function NewSourcePage() {
  const router = useRouter();

  const [name, setName] = useState("Demo Security File");
  const [sourceType, setSourceType] = useState("server");
  const [transport, setTransport] = useState("file");
  const [expectedFormat, setExpectedFormat] = useState("mixed");
  const [enabled, setEnabled] = useState(true);
  const [description, setDescription] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    setSubmitting(true);
    setError(null);
    try {
      const res = await postJSON<{ source_id: string }>("/sources", {
        name,
        source_type: sourceType,
        transport,
        expected_format: expectedFormat,
        enabled,
        description: description || null,
      });
      router.push(`/sources/${res.source_id}`);
    } catch (e) {
      setError(String(e));
      setSubmitting(false);
    }
  }

  return (
    <main className="main">
      <div className="page-head">
        <div>
          <h1>Add Log Source</h1>
          <p>Register a source in the Source Registry before uploading logs.</p>
        </div>
        <Link href="/sources" className="btn">← Sources</Link>
      </div>

      <div className="card" style={{ maxWidth: 560 }}>
        <label className="field">
          <span>Source Name</span>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. Demo Security File"
          />
        </label>

        <label className="field">
          <span>Source Type</span>
          <select value={sourceType} onChange={(e) => setSourceType(e.target.value)}>
            {SOURCE_TYPES.map((t) => (
              <option key={t.value} value={t.value}>
                {t.label}
              </option>
            ))}
          </select>
        </label>

        <label className="field">
          <span>Transport</span>
          <select value={transport} onChange={(e) => setTransport(e.target.value)}>
            {TRANSPORTS.map((t) => (
              <option key={t.value} value={t.value}>
                {t.label}
              </option>
            ))}
          </select>
        </label>
        {transport !== "file" && (
          <p className="form-note">
            Module 2 supports the File transport for onboarding. UDP/HTTP are
            existing demo collectors and are not dynamically provisioned here.
          </p>
        )}

        <label className="field">
          <span>Expected Format</span>
          <select
            value={expectedFormat}
            onChange={(e) => setExpectedFormat(e.target.value)}
          >
            {EXPECTED_FORMATS.map((f) => (
              <option key={f.value} value={f.value}>
                {f.label}
              </option>
            ))}
          </select>
        </label>

        <label className="field">
          <span>Description (optional)</span>
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            rows={2}
          />
        </label>

        <label className="field" style={{ display: "flex", gap: 10, alignItems: "center" }}>
          <input
            type="checkbox"
            checked={enabled}
            onChange={(e) => setEnabled(e.target.checked)}
          />
          <span style={{ margin: 0 }}>Enabled</span>
        </label>

        {error && <div className="error" style={{ marginBottom: 14 }}>{error}</div>}

        <button className="btn btn-primary" disabled={submitting || !name.trim()} onClick={submit}>
          {submitting ? "Creating…" : "Create Source"}
        </button>
      </div>
    </main>
  );
}