import { useState } from "react";
import { toast } from "sonner";
import { Check, CircleAlert, ClipboardCopy, FileCode2 } from "lucide-react";
import type { OcsfEvent } from "@shared/api";
import { Button } from "@/components/ui/button";

// Saturated hues, but dark enough to clear WCAG AA (4.5:1) against PANEL_BG.
// True neon cannot do this: #22FF88 green measures 1.16:1 on a light panel and
// #FF4FD8 pink 2.47:1. "Bright" here means vivid hue, not high lightness —
// these are the most vivid variants that still pass, and a spec pins the ratio
// so a future tweak cannot quietly regress it. Green carries the keys, so
// booleans moved to blue to keep every token visually distinct.
export const PANEL_BG = "#FFFFFF";

export const TONE = {
  key: "text-[#0B7A3E] font-semibold",
  string: "text-[#C2185B]",
  number: "text-[#B54708]",
  boolean: "text-[#1a56db] font-semibold",
  null: "text-[#B91C1C] font-semibold",
  plain: "text-[#5B6B85]",
} as const;

type Piece = { text: string; tone?: (typeof TONE)[keyof typeof TONE] };

/**
 * Split pretty-printed JSON into coloured tokens.
 *
 * A string is a key when it is immediately followed by a colon, otherwise it
 * is a value. Braces, commas and whitespace fall through unstyled.
 */
export function tokenize(json: string): Piece[] {
  const pattern =
    /("(?:\\.|[^"\\])*")(\s*:)?|\b(?:true|false)\b|\bnull\b|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?/g;
  const pieces: Piece[] = [];
  let cursor = 0;
  let match: RegExpExecArray | null;

  while ((match = pattern.exec(json)) !== null) {
    if (match.index > cursor) {
      pieces.push({ text: json.slice(cursor, match.index), tone: TONE.plain });
    }
    if (match[1] !== undefined) {
      pieces.push({ text: match[1], tone: match[2] ? TONE.key : TONE.string });
      if (match[2]) pieces.push({ text: match[2], tone: TONE.plain });
    } else if (match[0] === "null") {
      pieces.push({ text: match[0], tone: TONE.null });
    } else if (match[0] === "true" || match[0] === "false") {
      pieces.push({ text: match[0], tone: TONE.boolean });
    } else {
      pieces.push({ text: match[0], tone: TONE.number });
    }
    cursor = match.index + match[0].length;
  }
  if (cursor < json.length) {
    pieces.push({ text: json.slice(cursor), tone: TONE.plain });
  }
  return pieces;
}

export default function JsonViewer({ event, lineNumber, footnote }: { event: OcsfEvent | null; lineNumber?: number; footnote?: string }) {
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    if (!event) return;
    try {
      await navigator.clipboard.writeText(JSON.stringify(event, null, 2));
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      toast.error("Clipboard unavailable");
    }
  };

  return (
    <div>
      <div className="mb-2 flex items-center justify-between gap-2">
        <h2 className="flex items-center gap-2 text-base font-bold tracking-tight">
          <FileCode2 className="h-4 w-4 text-[#2f8ce0]" />
          Event JSON
        </h2>
        <Button variant="outline" size="sm" onClick={copy} disabled={!event}>
          {copied ? <Check className="h-3.5 w-3.5 text-emerald-500" /> : <ClipboardCopy className="h-3.5 w-3.5" />}
          {copied ? "Copied" : "Copy"}
        </Button>
      </div>
      {event ? (
        <div
          style={{ backgroundColor: PANEL_BG }}
          className="max-h-[70vh] overflow-auto rounded-lg border-[3px] border-[#25263A] p-4 shadow-[0_2px_10px_rgba(37,38,58,0.18)]"
        >
          <pre className="font-mono text-[12px] leading-relaxed">
            {tokenize(JSON.stringify(event, null, 2)).map((piece, i) => (
              <span key={i} className={piece.tone}>
                {piece.text}
              </span>
            ))}
          </pre>
        </div>
      ) : (
        <div className="flex flex-col items-center gap-2 rounded-lg border border-dashed border-border px-4 py-10 text-center text-sm text-muted-foreground">
          <CircleAlert className="h-5 w-5" />
          Select an event to inspect its OCSF payload.
        </div>
      )}
      {(footnote || lineNumber !== undefined) && (
        <p className="mt-2 break-words px-1 font-mono text-[10px] text-muted-foreground">
          {lineNumber !== undefined ? `line #${lineNumber} · ` : ""}
          {footnote ?? ""}
        </p>
      )}
    </div>
  );
}
