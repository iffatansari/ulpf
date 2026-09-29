import { afterEach, describe, expect, it, vi } from "vitest";
import {
  backendDlqToRejected,
  getBackendSourceEvents,
  getBackendSourceStats,
  isReprocessTerminal,
  listBackendDlq,
  mergeBackendEvents,
  pollReprocessRun,
  previewReprocessBatch,
  reprocessDlqBatch,
  reprocessDlqRecord,
  subscribeToBackendEvents,
  type BackendDlqRecord,
  type BackendNormalizedEvent,
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

/**
 * The live event stream and the source-scoped history both feed the same list,
 * so they have to agree on how a source is addressed: an id goes in the path
 * for history and in a query parameter for the stream. Getting that wrong
 * silently shows every source's events on a single source's page.
 */
describe("source-scoped event reads", () => {
  it("addresses source history by id in the path, encoded", async () => {
    const fetchMock = vi.fn(
      async (_url: string, _init?: RequestInit) =>
        jsonResponse({ total: 0, events: [] }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await getBackendSourceEvents("src web/1", 10);

    expect(fetchMock.mock.calls[0][0]).toBe("/backend/sources/src%20web%2F1/events?limit=10");
  });

  it("addresses source stats by the same encoded path segment", async () => {
    const fetchMock = vi.fn(
      async (_url: string, _init?: RequestInit) => jsonResponse({ source_id: "src web/1" }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await getBackendSourceStats("src web/1");

    expect(fetchMock.mock.calls[0][0]).toBe("/backend/sources/src%20web%2F1/stats");
  });

  it("addresses the event stream by id in a query parameter, encoded", () => {
    const streams: FakeEventSource[] = [];
    vi.stubGlobal("window", {});
    vi.stubGlobal(
      "EventSource",
      class extends FakeEventSource {
        constructor(url: string) {
          super(url);
          streams.push(this);
        }
      },
    );

    subscribeToBackendEvents("src web/1", { onEvent: () => {} });

    expect(streams[0].url).toBe("/backend/events/stream?source_id=src%20web%2F1");
  });

  it("subscribes to the unscoped stream when no source is selected", () => {
    const streams: FakeEventSource[] = [];
    vi.stubGlobal("window", {});
    vi.stubGlobal(
      "EventSource",
      class extends FakeEventSource {
        constructor(url: string) {
          super(url);
          streams.push(this);
        }
      },
    );

    subscribeToBackendEvents(undefined, { onEvent: () => {} });

    expect(streams[0].url).toBe("/backend/events/stream");
  });
});

class FakeEventSource {
  url: string;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  private listeners = new Map<string, Set<(event: Event) => void>>();

  constructor(url: string) {
    this.url = url;
  }

  addEventListener(type: string, handler: (event: Event) => void) {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    this.listeners.get(type)!.add(handler);
  }

  removeEventListener(type: string, handler: (event: Event) => void) {
    this.listeners.get(type)?.delete(handler);
  }

  emit(type: string, data: string) {
    for (const handler of this.listeners.get(type) ?? []) {
      handler({ data } as MessageEvent<string>);
    }
  }

  listenerCount(type: string): number {
    return this.listeners.get(type)?.size ?? 0;
  }

  close() {}
}

function event(over: Partial<BackendNormalizedEvent> = {}): BackendNormalizedEvent {
  return {
    event_id: "evt-1",
    raw_event_id: "bronze-1",
    parser_id: "json-parser-v1",
    time: "2026-09-27T10:00:00Z",
    class_name: "security_activity",
    ...over,
  } as BackendNormalizedEvent;
}

describe("SSE event handling", () => {
  function fakeStream(sourceId: string | undefined) {
    let created: FakeEventSource | null = null;
    // The client guards on window existing, and vitest runs these in node, so
    // both the guard and the constructor have to be stubbed to reach the code
    // under test.
    vi.stubGlobal("window", {});
    vi.stubGlobal(
      "EventSource",
      class extends FakeEventSource {
        constructor(url: string) {
          super(url);
          created = this;
        }
      },
    );
    const handlers = {
      onEvent: vi.fn(),
      onOpen: vi.fn(),
      onError: vi.fn(),
      onReset: vi.fn(),
    };
    subscribeToBackendEvents(sourceId, handlers);
    return { stream: created as unknown as FakeEventSource, handlers };
  }

  it("delivers a normalized event to onEvent", () => {
    const { stream, handlers } = fakeStream("src-1");

    stream.emit("normalized", JSON.stringify(event()));

    expect(handlers.onEvent).toHaveBeenCalledWith(event());
  });

  it("ignores a payload that is not a usable event instead of throwing", () => {
    const { stream, handlers } = fakeStream("src-1");

    stream.emit("normalized", JSON.stringify({ nope: true }));

    expect(handlers.onEvent).not.toHaveBeenCalled();
    expect(handlers.onError).not.toHaveBeenCalled();
  });

  it("reports malformed JSON as a stream error", () => {
    const { stream, handlers } = fakeStream("src-1");

    stream.emit("normalized", "{not json");

    expect(handlers.onEvent).not.toHaveBeenCalled();
    expect(handlers.onError).toHaveBeenCalled();
  });

  it("maps a reset event to onReset so the UI can refetch", () => {
    const { stream, handlers } = fakeStream("src-1");

    stream.emit("reset", "{}");

    expect(handlers.onReset).toHaveBeenCalled();
  });

  it("detaches its listeners and closes the stream on unsubscribe", () => {
    let created: FakeEventSource | null = null;
    vi.stubGlobal("window", {});
    vi.stubGlobal(
      "EventSource",
      class extends FakeEventSource {
        constructor(url: string) {
          super(url);
          created = this;
        }
      },
    );

    const unsubscribe = subscribeToBackendEvents("src-1", { onEvent: () => {} });
    const stream = created as unknown as FakeEventSource;
    expect(stream.listenerCount("normalized")).toBe(1);

    unsubscribe();

    expect(stream.listenerCount("normalized")).toBe(0);
    expect(stream.listenerCount("reset")).toBe(0);
  });
});

describe("merging streamed events into the visible list", () => {
  it("orders newest first across both sources", () => {
    const merged = mergeBackendEvents(
      [event({ event_id: "old", time: "2026-09-27T09:00:00Z" })],
      [event({ event_id: "new", time: "2026-09-27T11:00:00Z" })],
    );

    expect(merged.map((e) => e.event_id)).toEqual(["new", "old"]);
  });

  it("dedupes by event_id, letting the incoming copy win", () => {
    const merged = mergeBackendEvents(
      [event({ parser_id: "syslog-parser-v1" })],
      [event({ parser_id: "json-parser-v1" })],
    );

    expect(merged).toHaveLength(1);
    expect(merged[0].parser_id).toBe("json-parser-v1");
  });

  it("caps the list at the limit, keeping the newest", () => {
    const merged = mergeBackendEvents(
      [],
      [
        event({ event_id: "a", time: "2026-09-27T09:00:00Z" }),
        event({ event_id: "b", time: "2026-09-27T11:00:00Z" }),
        event({ event_id: "c", time: "2026-09-27T10:00:00Z" }),
      ],
      2,
    );

    expect(merged.map((e) => e.event_id)).toEqual(["b", "c"]);
  });
});
