import type { RequestHandler } from "express";

/**
 * Same-origin proxy to the ULPF FastAPI backend.
 *
 * The UI is a single-port app (dev :8080, prod :3000). Instead of making
 * the browser talk to FastAPI directly (which would need CORS everywhere),
 * we mount this handler at /backend/* — it forwards the request to
 * BACKEND_URL (default http://localhost:8000) and streams the JSON back.
 *
 * It must be registered BEFORE the JSON body parsers in createServer():
 * those would consume the request stream and break file uploads.
 */

const BACKEND_BASE = (
  process.env.BACKEND_URL ?? "http://localhost:8000"
).replace(/\/+$/, "");
const MAX_PROXY_BODY_BYTES = Math.max(
  1024,
  Number(process.env.MAX_PROXY_BODY_BYTES ?? 100 * 1024 * 1024),
);

const SAFE_HEADERS = [
  "content-type",
  "accept",
  "authorization",
  "x-request-id",
  "last-event-id",
];
const BODYLESS = new Set(["GET", "HEAD"]);

class ProxyRequestError extends Error {
  constructor(
    message: string,
    readonly statusCode: number,
  ) {
    super(message);
    this.name = "ProxyRequestError";
  }
}

function readBody(req: Parameters<RequestHandler>[0]): Promise<Buffer> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];
    let total = 0;
    req.on("data", (chunk) => {
      const buffer = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
      total += buffer.length;
      if (total > MAX_PROXY_BODY_BYTES) {
        reject(new ProxyRequestError("request body exceeds proxy limit", 413));
        req.destroy();
        return;
      }
      chunks.push(buffer);
    });
    req.on("end", () => resolve(Buffer.concat(chunks)));
    req.on("aborted", () =>
      reject(new ProxyRequestError("request was aborted", 400)),
    );
    req.on("error", reject);
  });
}

function waitForDrain(res: Parameters<RequestHandler>[1]): Promise<void> {
  return new Promise((resolve) => {
    let settled = false;
    const cleanup = () => {
      res.off("drain", onDrain);
      res.off("close", onClose);
    };
    const finish = () => {
      if (settled) return;
      settled = true;
      cleanup();
      resolve();
    };
    const onDrain = () => finish();
    const onClose = () => finish();
    res.once("drain", onDrain);
    res.once("close", onClose);
  });
}

export const handleBackend: RequestHandler = async (req, res) => {
  const target = `${BACKEND_BASE}${req.url}`;
  const headers: Record<string, string> = {};
  for (const name of SAFE_HEADERS) {
    const value = req.headers[name];
    if (typeof value === "string") headers[name] = value;
  }

  const controller = new AbortController();
  let reader: ReadableStreamDefaultReader<Uint8Array> | undefined;
  let downstreamClosed = false;
  const onDownstreamClose = () => {
    downstreamClosed = true;
    controller.abort();
    void reader?.cancel().catch(() => undefined);
  };
  res.once("close", onDownstreamClose);

  try {
    const rawBody = BODYLESS.has(req.method) ? undefined : await readBody(req);
    const upstream = await fetch(target, {
      method: req.method,
      headers,
      body: rawBody && rawBody.length ? new Uint8Array(rawBody) : undefined,
      signal: controller.signal,
    });

    const contentType = upstream.headers.get("content-type") ?? "";
    res.status(upstream.status);
    if (contentType) res.setHeader("content-type", contentType);
    if (contentType.includes("text/event-stream")) {
      res.setHeader("cache-control", "no-cache");
      res.setHeader("connection", "keep-alive");
      res.setHeader("x-accel-buffering", "no");
      if (!upstream.body) {
        res.end();
        return;
      }
      reader = upstream.body.getReader();
      try {
        while (true) {
          if (res.destroyed || res.writableEnded || downstreamClosed) {
            await reader.cancel().catch(() => undefined);
            break;
          }
          const { done, value } = await reader.read();
          if (done) break;
          if (value && !res.write(Buffer.from(value))) {
            await waitForDrain(res);
          }
        }
      } finally {
        await reader.cancel().catch(() => undefined);
        reader.releaseLock();
        reader = undefined;
      }
      res.end();
      return;
    }

    const text = await upstream.text();
    if (text) res.send(text);
    else res.end();
  } catch (err) {
    if (downstreamClosed || controller.signal.aborted) {
      res.destroy();
      return;
    }
    if (res.headersSent) {
      res.destroy();
      return;
    }
    if (err instanceof ProxyRequestError) {
      res.status(err.statusCode).json({ error: err.message });
      return;
    }
    const message = err instanceof Error ? err.message : String(err);
    res
      .status(502)
      .json({ error: `backend unreachable (${BACKEND_BASE}): ${message}` });
  } finally {
    res.off("close", onDownstreamClose);
  }
};
