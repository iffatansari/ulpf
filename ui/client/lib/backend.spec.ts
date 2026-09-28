import { describe, expect, it } from "vitest";
import {
  backendSourceToUi,
  endpointForTransport,
  uiTransportForBackend,
  type BackendSourceDoc,
} from "./backend";

const source: BackendSourceDoc = {
  source_id: "sse-source-1",
  name: "Application stream",
  source_type: "application",
  transport: "sse",
  expected_format: "json",
  enabled: true,
  created_at: "2026-09-25T10:00:00Z",
};

describe("SSE source mapping", () => {
  it("maps the backend SSE transport to the UI", () => {
    expect(uiTransportForBackend("sse")).toBe("sse_stream");
    expect(endpointForTransport("sse")).toBe("https://<upstream-host>/events");
  });

  it("uses the JSON parser chain for SSE sources", () => {
    expect(backendSourceToUi(source)).toMatchObject({
      id: "sse-source-1",
      transport: "sse_stream",
      parser_chain: ["JSON Parser", "Key/Value Parser", "Text Parser"],
    });
  });
});
