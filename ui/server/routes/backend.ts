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

const BACKEND_BASE = (process.env.BACKEND_URL ?? "http://localhost:8000").replace(/\/+$/, "");

const SAFE_HEADERS = ["content-type", "accept", "authorization", "x-request-id"];
const BODYLESS = new Set(["GET", "HEAD"]);

function readBody(req: Parameters<RequestHandler>[0]): Promise<Buffer> {
  return new Promise((resolve) => {
    const chunks: Buffer[] = [];
    req.on("data", (chunk) => chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk)));
    req.on("end", () => resolve(Buffer.concat(chunks)));
    req.on("error", () => resolve(Buffer.concat(chunks)));
  });
}

export const handleBackend: RequestHandler = async (req, res) => {
  const target = `${BACKEND_BASE}${req.url}`;
  const headers: Record<string, string> = {};
  for (const name of SAFE_HEADERS) {
    const value = req.headers[name];
    if (typeof value === "string") headers[name] = value;
  }

  try {
    const rawBody = BODYLESS.has(req.method) ? undefined : await readBody(req);
    const upstream = await fetch(target, {
      method: req.method,
      headers,
      body: rawBody && rawBody.length ? new Uint8Array(rawBody) : undefined,
    });

    const text = await upstream.text();
    res.status(upstream.status);
    res.type("application/json");
    if (text) res.send(text);
    else res.end();
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    res.status(502).json({ error: `backend unreachable (${BACKEND_BASE}): ${message}` });
  }
};