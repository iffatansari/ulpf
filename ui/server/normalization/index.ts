import { LineResult, LogFormat, NormalizeRequest, NormalizeResponse, NormalizationSummary, OcsfEvent } from "../../shared/api";
import { ParsedEvent, detectFormat, parseApacheLine, parseCefLine, parseJsonLine, parseKeyValueLine, parseLeefLine, parseSyslogLine, parseTextLine } from "./parsers";
import { classifyParsed } from "./classify";

const MAX_LINE_LENGTH = 100_000;

interface SplitResult {
  lines: string[];
  skipped: number;
}

/**
 * JSON lines are often pretty-printed across many physical lines. Coalesce
 * balanced {..} / [..] spans produced by json.dumps(..., indent=2) style logs.
 * Everything else is treated as one logical line per physical line.
 */
export function splitContent(content: string): SplitResult {
  const physical = content.split(/\r\n|\r|\n/);
  const out: string[] = [];
  let skipped = 0;
  let buf = "";
  let depth = 0;
  let inString = false;
  let escape = false;

  const flush = () => {
    if (buf.trim() !== "") out.push(buf);
    buf = "";
  };

  for (const rawLine of physical) {
    const line = rawLine;
    if (buf === "") {
      const trimmed = line.trim();
      if (trimmed === "") continue;
      if (trimmed.length > MAX_LINE_LENGTH) {
        skipped += 1;
        continue;
      }
      if (trimmed.startsWith("{") || trimmed.startsWith("[")) {
        // Start a JSON coalescing span.
        buf = line;
        depth = 0;
        inString = false;
        escape = false;
        for (const ch of trimmed) {
          if (inString) {
            if (escape) escape = false;
            else if (ch === "\\") escape = true;
            else if (ch === '"') inString = false;
          } else if (ch === '"') {
            inString = true;
          } else if (ch === "{") depth += 1;
          else if (ch === "}") depth -= 1;
          else if (ch === "[") depth += 1;
          else if (ch === "]") depth -= 1;
        }
        if (depth <= 0) flush();
        continue;
      }
      out.push(line);
      continue;
    }

    // Inside a JSON span: accumulate until balanced.
    let lineDepth = depth;
    for (const ch of line) {
      if (inString) {
        if (escape) escape = false;
        else if (ch === "\\") escape = true;
        else if (ch === '"') inString = false;
      } else if (ch === '"') {
        inString = true;
      } else if (ch === "{") lineDepth += 1;
      else if (ch === "}") lineDepth -= 1;
      else if (ch === "[") lineDepth += 1;
      else if (ch === "]") lineDepth -= 1;
    }
    depth = lineDepth;
    buf += "\n" + line;
    if (depth <= 0) flush();
  }

  // Unterminated JSON span: flush as-is; the JSON parser will reject it loudly.
  flush();
  return { lines: out, skipped };
}

/**
 * Multi-parser fallback chain: each line starts at its primary format parser,
 * then walks generic secondary/fallback parsers. Every attempt is recorded so
 * the UI can distinguish "the primary parser gave up" (rescued by a later
 * parser) from "truly unparseable" (rejected with the full list of parsers
 * tried). Primary for the requested/detected format still wins; acceptance
 * semantics for clean lines are unchanged.
 */
interface ChainParser {
  label: string;
  format: Exclude<LogFormat, "auto">;
  parse: (line: string) => ParsedEvent[] | null;
}

const one = (parse: (line: string) => ParsedEvent | null): ChainParser["parse"] => (line) => {
  const p = parse(line);
  return p ? [p] : null;
};

const CHAINS: Record<Exclude<LogFormat, "auto">, ChainParser[]> = {
  json: [{ label: "JSON Parser", format: "json", parse: parseJsonLine }],
  cef: [{ label: "CEF Parser", format: "cef", parse: one(parseCefLine) }],
  leef: [{ label: "LEEF Parser", format: "leef", parse: one(parseLeefLine) }],
  syslog: [{ label: "Syslog Parser", format: "syslog", parse: one(parseSyslogLine) }],
  apache: [{ label: "HTTP Access Log Parser", format: "apache", parse: one(parseApacheLine) }],
  keyvalue: [{ label: "Key/Value Parser", format: "keyvalue", parse: one(parseKeyValueLine) }],
  text: [{ label: "Text Parser", format: "text", parse: one(parseTextLine) }],
};

// Generic rescue parsers appended after the primary one fails.
const GENERIC_FALLBACKS: ChainParser[] = [
  { label: "Key/Value Parser", format: "keyvalue", parse: one(parseKeyValueLine) },
  { label: "Text Parser", format: "text", parse: one(parseTextLine) },
];

function chainForLine(format: LogFormat | "auto", line: string): ChainParser[] {
  const primary = format === "auto" ? detectFormat(line) : format;
  const head = CHAINS[primary];
  const tail = GENERIC_FALLBACKS.filter((f) => f.format !== primary);
  return [...head, ...tail];
}

interface ParseAttempt {
  parsedList: ParsedEvent[] | null;
  tried: string[];
  chainRescued: boolean;
}

function parseWithChain(format: LogFormat | "auto", line: string): ParseAttempt {
  const attempted = chainForLine(format, line);
  const tried: string[] = [];
  for (let i = 0; i < attempted.length; i++) {
    const cp = attempted[i];
    tried.push(cp.label);
    const parsedList = cp.parse(line);
    if (parsedList && parsedList.length > 0) {
      return { parsedList, tried, chainRescued: i > 0 };
    }
  }
  return { parsedList: null, tried, chainRescued: false };
}

function reject(lineNumber: number, line: string | null, reason: string, tried?: string[]): LineResult {
  return { ok: false, reason, line, line_number: lineNumber, ...(tried ? { tried_parsers: tried } : {}) };
}

export function normalizeLogInput(req: NormalizeRequest): NormalizeResponse {
  const content = req.content ?? "";
  const requestedFormat = req.source?.format ?? "auto";
  const sourceName = req.source?.name || undefined;

  const { lines, skipped } = splitContent(content);
  const results: LineResult[] = [];
  const byClass: Record<string, number> = {};
  const byFormat: Record<string, number> = {};
  const formatsSeen = new Set<LogFormat>();
  let rescuedCount = 0;
  const ingestTime = Date.now();

  for (let i = 0; i < lines.length; i++) {
    const lineNo = i + 1;
    const line = lines[i];
    if (!line || line.trim() === "") {
      results.push(reject(lineNo, null, "empty line"));
      continue;
    }

    const { parsedList, tried, chainRescued } = parseWithChain(requestedFormat, line);
    if (!parsedList || parsedList.length === 0) {
      results.push(reject(lineNo, line, "no parsable log data", tried));
      continue;
    }

    for (const parsed of parsedList) {
      formatsSeen.add(parsed.format);
      byFormat[parsed.format] = (byFormat[parsed.format] ?? 0) + 1;
      const event = classifyParsed(parsed, ingestTime, sourceName);
      if (!event) {
        results.push(reject(lineNo, line, "empty event after classification", tried));
        continue;
      }
      byClass[event.class_name ?? "Uncategorized"] = (byClass[event.class_name ?? "Uncategorized"] ?? 0) + 1;
      if (chainRescued) rescuedCount += 1;
      results.push({
        ok: true,
        event,
        source_line: line,
        line_number: lineNo,
        format: parsed.format,
        parser: parsed.parser,
        parser_chain: tried,
        chain_rescued: chainRescued,
        parsed: [dumpParsed(parsed)],
      });
    }
  }

  const events = results.filter((r) => r.ok).length;
  const rejected = results.filter((r) => !r.ok).length;
  const summary: NormalizationSummary = {
    total_lines: lines.length,
    events,
    rejected,
    skipped,
    rescued: rescuedCount,
    source_format: formatsSeen.size === 1 ? [...formatsSeen][0] : formatsSeen.size > 1 ? "mixed" : "auto",
    by_class: byClass,
    by_format: byFormat,
  };

  return { ok: true, lines: results, summary };
}

function dumpParsed(parsed: ParsedEvent): Record<string, unknown> {
  const { raw, format, parser, parsed_objects, unmapped, ...rest } = parsed;
  void raw;
  void format;
  void parser;
  void parsed_objects;
  return { ...rest, unmapped: Object.keys(unmapped).length > 0 ? unmapped : undefined };
}

export function eventToLine(event: OcsfEvent): string {
  return JSON.stringify(event);
}