import { describe, expect, it } from "vitest";
import { collectorSnippet, reconcile, type Source } from "./source-context";
import type { BackendSourceDoc } from "./backend";

const sseSource: Source = {
  id: "sse-source-1",
  name: "Application stream",
  typeId: "application",
  transport: "sse_stream",
  endpoint: "https://events.example.test/stream",
  format: "auto",
  parser_chain: ["JSON Parser"],
  createdAt: Date.now(),
};

describe("SSE collector snippets", () => {
  it("uses the compose profile and an explicit host allowlist for containers", () => {
    const snippet = collectorSnippet(sseSource, "container");

    expect(snippet).toContain(
      "docker compose --profile sse up --build -d sse_collector",
    );
    expect(snippet).toContain(
      "UPSTREAM_SSE_URL='https://events.example.test/stream'",
    );
    expect(snippet).toContain("SSE_ALLOWED_HOSTS='events.example.test'");
    expect(snippet).toContain("SSE_SOURCE_ID='sse-source-1'");
    expect(snippet).toContain("SSE_SOURCE_TYPE='application'");
    expect(snippet).not.toContain("ulpf-sse-collector:latest");
    expect(snippet).not.toContain("$SSE_BEARER_TOKEN");
  });

  it("exports allowlisted, quoted variables for the agent mode", () => {
    const snippet = collectorSnippet(sseSource, "agent");

    expect(snippet).toContain(
      "export UPSTREAM_SSE_URL='https://events.example.test/stream'",
    );
    expect(snippet).toContain("export SSE_ALLOWED_HOSTS='events.example.test'");
    expect(snippet).toContain("export SSE_SOURCE_ID='sse-source-1'");
    expect(snippet).toContain("python -m collectors.sse_collector.main");
  });

  it("escapes single quotes in identifiers", () => {
    const snippet = collectorSnippet(
      { ...sseSource, id: "o'brien-sse" },
      "agent",
    );

    expect(snippet).toContain("export SSE_SOURCE_ID='o'\\''brien-sse'");
  });

  it("falls back to a placeholder host when the endpoint is not a URL", () => {
    const snippet = collectorSnippet(
      { ...sseSource, endpoint: "not-a-url" },
      "agent",
    );

    expect(snippet).toContain("export SSE_ALLOWED_HOSTS='<upstream-host>'");
  });
});

const doc = (source_id: string, name: string): BackendSourceDoc =>
  ({
    source_id,
    name,
    source_type: "application",
    transport: "kafka_sim",
    expected_format: "mixed",
    enabled: true,
    created_at: "2026-01-01T00:00:00Z",
  }) as BackendSourceDoc;

describe("registry reconciliation", () => {
  it("keeps the browser-only lastRun stats a plain remap would drop", () => {
    const lastRun = {
      total: 12,
      accepted: 10,
      rejected: 1,
      rescued: 1,
      at: Date.now(),
    };
    const local: Source[] = [
      { ...sseSource, id: "sim-1", name: "Sim", lastRun },
    ];

    const merged = reconcile([doc("sim-1", "Sim")], local);

    expect(merged).toHaveLength(1);
    expect(merged[0].lastRun).toEqual(lastRun);
  });

  it("takes the registry as truth for the fields it owns", () => {
    const local: Source[] = [{ ...sseSource, id: "sim-1", name: "Stale name" }];

    const merged = reconcile([doc("sim-1", "Renamed")], local);

    expect(merged[0].name).toBe("Renamed");
  });

  it("drops sources deleted server-side instead of leaving a ghost", () => {
    const local: Source[] = [
      { ...sseSource, id: "sim-old", name: "Deleted" },
      { ...sseSource, id: "sim-1", name: "Sim" },
    ];

    const merged = reconcile([doc("sim-1", "Sim")], local);

    expect(merged.map((s) => s.id)).toEqual(["sim-1"]);
  });

  it("adopts sources created server-side that the browser has never seen", () => {
    const merged = reconcile([doc("sim-new", "Brand new")], []);

    expect(merged).toHaveLength(1);
    expect(merged[0].id).toBe("sim-new");
  });
});
