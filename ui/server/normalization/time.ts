/**
 * Timestamp -> epoch milliseconds, with optional timezone offset in minutes.
 * Supports the formats most commonly seen in logs:
 *  - numeric epoch (s / ms / us)
 *  - ISO 8601 ("2024-01-22T12:42:48Z" / with offset)
 *  - "YYYY-MM-DD HH:MM:SS[.sss] [+/-HH:MM]"
 *  - RFC 1123 / HTTP ("Mon, 22 Jan 2024 12:42:48 GMT")
 *  - RFC 3164 syslog ("Jan 22 12:43:02", "Jan  2 09:00:00")
 *  - RFC 5424 ("2024-01-22T12:42:48.123Z")
 *  - Apache CLF ("22/Jan/2024:12:42:48 +0000")
 */
export interface ParsedTime {
  ms: number;
  tzOffset?: number;
}

const MONTHS: Record<string, number> = { jan: 0, feb: 1, mar: 2, apr: 3, may: 4, jun: 5, jul: 6, aug: 7, sep: 8, oct: 9, nov: 10, dec: 11 };

export function toIso(ms: number): string {
  return new Date(ms).toISOString();
}

function parseOffset(offset: string | undefined): number | undefined {
  if (!offset) return undefined;
  const m = /^([+-])(\d{1,2}):?(\d{2})?$/.exec(offset.trim());
  if (!m) return undefined;
  const hours = Number(m[2]);
  const minutes = Number(m[3] ?? "0");
  if (hours > 23 || minutes > 59) return undefined;
  const total = hours * 60 + minutes;
  return m[1] === "-" ? -total : total;
}

export function parseTimestamp(value: unknown): ParsedTime | undefined {
  if (value === undefined || value === null || value === "") return undefined;

  if (typeof value === "number") return toParsed(value);
  if (typeof value !== "string") return undefined;

  const s = value.trim();
  if (s === "") return undefined;

  // Numeric epoch
  if (/^[+-]?\d{8,}(\.\d+)?$/.test(s)) {
    const ms = parseNumeric(s);
    return ms === undefined ? undefined : toParsed(ms);
  }

  // "YYYY-MM-DD HH:MM:SS" / ISO style
  const dt = /^(\d{4}-\d{2}-\d{2})[ T](\d{1,2}:\d{2}:\d{2}(?:\.\d{1,9})?)(\s*Z|[+-]\d{2}:?\d{2})?$/.exec(s);
  if (dt) {
    const offset = dt[3]?.trim();
    const zulu = offset === "Z";
    const body = `${dt[1]}T${dt[2]}${zulu ? "Z" : offset ? offset.replace(/^([+-]\d{1,2}):(\d{2})$/, "$1$2") : "Z"}`;
    const ms = Date.parse(body);
    if (Number.isFinite(ms)) return { ms, tzOffset: zulu ? 0 : parseOffset(offset) };
    return undefined;
  }

  // RFC 3164 syslog
  const sys3164 = /^([A-Za-z]{3})\s{1,2}(\d{1,2})\s+(\d{2}):(\d{2}):(\d{2})(?:\s+(\d{4}))?(\s|$)/.exec(s);
  if (sys3164) {
    const month = MONTHS[sys3164[1].toLowerCase()];
    if (month !== undefined) {
      const now = new Date();
      let year = sys3164[6] ? Number(sys3164[6]) : now.getFullYear();
      const ms = new Date(year, month, Number(sys3164[2]), Number(sys3164[3]), Number(sys3164[4]), Number(sys3164[5]), 0).getTime();
      if (!sys3164[6] && ms > Date.now()) {
        // New year rollover (e.g. "Dec 31 ..." read in January) -> previous year.
        return { ms: new Date(year - 1, month, Number(sys3164[2]), Number(sys3164[3]), Number(sys3164[4]), Number(sys3164[5]), 0).getTime() };
      }
      return { ms };
    }
    return undefined;
  }

  // Apache CLF "22/Jan/2024:12:42:48 +0000"
  const aclf = /^\[(\d{2})\/([A-Za-z]{3})\/(\d{4}):(\d{2}):(\d{2}):(\d{2})\s+([+-]\d{4})\]/.exec(s);
  if (aclf) {
    const month = MONTHS[aclf[2].toLowerCase()];
    if (month !== undefined) {
      const year = Number(aclf[3]);
      const off = `${aclf[7].slice(0, 3)}:${aclf[7].slice(3)}`;
      const ms = Date.parse(`${aclf[1]} ${aclf[2]} ${year} ${aclf[4]}:${aclf[5]}:${aclf[6]} ${off}`);
      if (Number.isFinite(ms)) return { ms, tzOffset: parseOffset(aclf[7]) };
    }
    return undefined;
  }

  // RFC 1123 / 2822 and anything else `Date.parse` understands
  const ms = Date.parse(s);
  if (Number.isFinite(ms)) return { ms };

  return undefined;
}

function parseNumeric(value: string): number | undefined {
  const n = Number(value);
  if (!Number.isFinite(n) || n === 0) return undefined;
  if (n < 1e11) return Math.round(n * 1000); // seconds
  if (n < 1e14) return Math.round(n); // milliseconds
  if (n < 1e17) return Math.round(n / 1000); // microseconds
  return undefined;
}

function toParsed(ms: number): ParsedTime {
  return { ms };
}