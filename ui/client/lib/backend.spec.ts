import { afterEach, describe, expect, it, vi } from "vitest";
import {
  backendDlqToRejected,
  isReprocessTerminal,
  listBackendDlq,
  pollReprocessRun,
  previewReprocessBatch,
  reprocessDlqBatch,
  reprocessDlqRecord,
  type BackendDlqRecord,
  type ReprocessRun,
} from "./backend";

const record: BackendDlqRecord = {
  dlq_id: "dlq-1",
  raw_event_id: "bronze-1",
  parsers_attempted: ["syslog-v1", "json-v1"],
  status: "parse_failure",
  classification: "no_timestamp",
  first_seen_at: "2026-09-27T10:00:00Z",
  last_attempt_at: "2026-09-27T10:05:00Z",
  reprocess_count: 1,
  resolution_status: "unresolved",
  attempt_history: [
    {
      attempt: 1,
      reprocess_id: "run-1",
      started_at: "2026-09-27T10:05:00Z",
      result: "failed",
    },
  ],
};

function run(over: Partial<ReprocessRun> = {}): ReprocessRun {
  return {
    reprocess_id: "run-1",
    status: "running",
    requested_count: 1,
    published_count: 1,
    recovered_count: 0,
    failed_count: 0,
    dlq_ids: ["dlq-1"],
    event_ids: ["bronze-1"],
    errors: [],
    ...over,
  };
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function stubFetchSequence(...bodies: unknown[]) {
  let call = 0;
  const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => {
    const body = bodies[Math.min(call, bodies.length - 1)];
    call += 1;
    return jsonResponse(body);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function sentBody(init?: RequestInit): Record<string, unknown> {
  return JSON.parse(String(init?.body ?? "{}")) as Record<string, unknown>;
}

describe("reprocess client", () => {
  it("addresses a single record by dlq_id, never by row index", async () => {
    const fetchMock = stubFetchSequence(run({ status: "running" }));
    await reprocessDlqRecord("dlq-1", "upgraded syslog parser");

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/backend/dlq/dlq-1/reprocess");
    expect(init.method).toBe("POST");
    expect(sentBody(init)).toEqual({ reason: "upgraded syslog parser" });
  });

  it("always sends explicit ids for a batch", async () => {
    const fetchMock = stubFetchSequence(run({ requested_count: 2, published_count: 2 }));
    await reprocessDlqBatch(["dlq-1", "dlq-2"]);

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/backend/dlq/reprocess");
    expect((sentBody(init).dlq_ids as string[])).toEqual(["dlq-1", "dlq-2"]);
  });

  it("asks for a dry run without publishing", async () => {
    const fetchMock = stubFetchSequence({
      dry_run: true,
      requested: 1,
      would_succeed: 0,
      drain3_dependent: 1,
      results: [
        {
          dlq_id: "dlq-1",
          dry_run: true,
          would_succeed: false,
          drain3_dependent: true,
          note: "only a real replay is conclusive",
        },
      ],
    });
    const result = await previewReprocessBatch(["dlq-1"]);

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/backend/dlq/reprocess?dry_run=true");
    expect(sentBody(init).dry_run).toBe(true);
    expect(result.drain3_dependent).toBe(1);
  });

  it("polls until the run reaches a terminal status", async () => {
    stubFetchSequence(
      run({ status: "running", recovered_count: 0 }),
      run({ status: "running", recovered_count: 0 }),
      run({ status: "completed", recovered_count: 1 }),
    );
    const seen: string[] = [];
    const settled = await pollReprocessRun("run-1", {
      intervalMs: 0,
      onUpdate: (r) => seen.push(r.status),
    });

    expect(seen).toEqual(["running", "running", "completed"]);
    expect(settled.recovered_count).toBe(1);
  });

  it("treats partial and failed as terminal, running as not", () => {
    expect(isReprocessTerminal("completed")).toBe(true);
    expect(isReprocessTerminal("partial")).toBe(true);
    expect(isReprocessTerminal("failed")).toBe(true);
    expect(isReprocessTerminal("running")).toBe(false);
    expect(isReprocessTerminal("queued")).toBe(false);
  });

  it("surfaces the backend detail message on failure", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ detail: "bronze event not found: bronze-9" }, 404)),
    );
    await expect(reprocessDlqRecord("dlq-1")).rejects.toThrow(
      "bronze event not found: bronze-9",
    );
  });
});

describe("DLQ display adapter", () => {
  it("carries dlq_id through so a row can be addressed for replay", () => {
    expect(backendDlqToRejected(record, 4)).toMatchObject({
      reason: "no_timestamp",
      line_number: 5,
      dlq_id: "dlq-1",
      tried_parsers: ["syslog-v1", "json-v1"],
    });
  });
});
