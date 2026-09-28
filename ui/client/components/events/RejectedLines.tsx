import type { RejectedEventResult } from "@shared/api";
import { CheckCircle2, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

interface RejectedLinesProps {
  rejected: RejectedEventResult[];
  title?: string;
  emptyMessage?: string;
  /**
   * Total rejected count when `rejected` is a truncated window of it, so the
   * heading can report the real number instead of the page size.
   */
  totalCount?: number;
  maxHeightClass?: string;
}

export default function RejectedLines({
  rejected,
  title = "Rejected lines",
  emptyMessage = "Everything parsed cleanly — nothing rejected.",
  totalCount,
  maxHeightClass = "max-h-64",
}: RejectedLinesProps) {
  const total = totalCount ?? rejected.length;
  const truncated = total > rejected.length;

  return (
    <section>
      <h2 className="mb-2 flex flex-wrap items-center gap-2 text-base font-bold tracking-tight">
        <X className="h-4 w-4 text-rose-500" />
        {title}
        <span className="text-sm font-normal text-muted-foreground">({total})</span>
        {truncated && (
          <span className="text-xs font-normal text-muted-foreground">
            showing {rejected.length} most recent
          </span>
        )}
      </h2>

      {rejected.length === 0 ? (
        <div className="flex items-center gap-2 rounded-lg border border-border bg-card px-4 py-3 text-sm text-muted-foreground">
          <CheckCircle2 className="h-4 w-4 text-emerald-500" />
          {emptyMessage}
        </div>
      ) : (
        <div className={cn("overflow-y-auto rounded-lg border border-border bg-card", maxHeightClass)}>
          {rejected.map((r, index) => (
            <div
              key={`${r.line_number}-${index}`}
              className="flex gap-3 border-b border-border px-3 py-2.5 last:border-0"
            >
              <span className="w-10 shrink-0 pt-0.5 font-mono text-[10px] text-muted-foreground">
                #{r.line_number}
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-1.5">
                  <Badge variant="destructive" className="px-1.5 py-0.5 text-[10px]">
                    {r.reason}
                  </Badge>
                  {r.tried_parsers && r.tried_parsers.length > 0 && (
                    <Badge
                      variant="outline"
                      className="px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground"
                      title="Every parser in the fallback chain was attempted before this line was quarantined."
                    >
                      {r.tried_parsers.join(" → ")}
                    </Badge>
                  )}
                </div>
                {r.line ? (
                  <p className="mt-1 line-clamp-2 break-words font-mono text-[11px] text-muted-foreground">
                    {r.line}
                  </p>
                ) : (
                  <p className="mt-1 font-mono text-[11px] italic text-muted-foreground/70">
                    empty line
                  </p>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
