import { RequestHandler } from "express";
import { NormalizeResponse } from "@shared/api";
import { normalizeLogInput } from "../normalization";

const MAX_BODY_CHARS = 25_000_000;

export const handleNormalize: RequestHandler = (req, res) => {
  const body = (req.body ?? {}) as { content?: unknown; source?: { name?: unknown; format?: unknown } };
  const content = typeof body.content === "string" ? body.content : "";
  if (content.length > MAX_BODY_CHARS) {
    const response: NormalizeResponse = {
      ok: false,
      error: "Upload is too large (limit 25 MB)",
      lines: [],
      summary: { total_lines: 0, events: 0, rejected: 0, skipped: 0, rescued: 0, source_format: "auto", by_class: {}, by_format: {} },
    };
    res.status(413).json(response);
    return;
  }

  const sourceName = typeof body.source?.name === "string" ? body.source.name : undefined;
  const format = typeof body.source?.format === "string" ? body.source.format : "auto";
  const validFormats = ["auto", "json", "syslog", "cef", "leef", "keyvalue", "apache", "text"];
  const selected = validFormats.includes(format) ? (format as "auto" | "json" | "syslog" | "cef" | "leef" | "keyvalue" | "apache" | "text") : "auto";

  const response: NormalizeResponse = normalizeLogInput({ content, source: sourceName ? { name: sourceName, format: selected } : { format: selected } });
  res.status(200).json(response);
};