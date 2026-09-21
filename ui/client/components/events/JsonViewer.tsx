import { useState } from "react";
import { toast } from "sonner";
import { Check, CircleAlert, ClipboardCopy, FileCode2 } from "lucide-react";
import type { OcsfEvent } from "@shared/api";
import { Button } from "@/components/ui/button";

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
        <div className="overflow-x-auto rounded-lg border border-[#E4E8F2] bg-[#25263A] p-3">
          <pre className="font-mono text-[11px] leading-relaxed text-[#E8F4FF]">{JSON.stringify(event, null, 2)}</pre>
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