import { useRef, useState } from "react";
import { toast } from "sonner";
import { FileUp, Loader2, RefreshCw } from "lucide-react";
import type { LogFormat } from "@shared/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { FORMATS, SAMPLES } from "@/lib/samples";
import { useNormalizer } from "@/lib/normalize-context";
import { cn } from "@/lib/utils";

export default function NormalizerForm({ compact = false, onDone }: { compact?: boolean; onDone?: () => void }) {
  const { run, loading } = useNormalizer();
  const [content, setContent] = useState("");
  const [sourceName, setSourceName] = useState("");
  const [format, setFormat] = useState<LogFormat | "auto">("auto");
  const [mode, setMode] = useState<"paste" | "upload">("paste");
  const [fileName, setFileName] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const onFile = (file: File | null) => {
    if (!file) return;
    if (file.size > 20 * 1024 * 1024) {
      toast.error("File too large — paste content instead (max ~20 MB)");
      return;
    }
    setFileName(file.name);
    if (file.name.endsWith(".json")) setFormat("json");
    const reader = new FileReader();
    reader.onload = () => {
      setContent(String(reader.result ?? ""));
      setMode("paste");
    };
    reader.onerror = () => toast.error("Could not read file");
    reader.readAsText(file);
  };

  const submit = async () => {
    const data = await run({ content, sourceName, format });
    if (data) onDone?.();
  };

  return (
    <Card>
      <CardHeader className="pb-4">
        <div className="flex items-center justify-between">
          <CardTitle className="text-base">Input</CardTitle>
          {!compact && (
            <div className="flex gap-1 rounded-md border border-border bg-muted/60 p-0.5">
              <button
                onClick={() => setMode("paste")}
                className={cn("rounded px-2.5 py-1 text-xs font-medium transition-colors", mode === "paste" ? "bg-card shadow-sm" : "text-muted-foreground")}
              >
                Paste
              </button>
              <button
                onClick={() => setMode("upload")}
                className={cn("rounded px-2.5 py-1 text-xs font-medium transition-colors", mode === "upload" ? "bg-card shadow-sm" : "text-muted-foreground")}
              >
                Upload file
              </button>
            </div>
          )}
        </div>
        <CardDescription>Roughly one event per line. Pretty-printed JSON is coalesced automatically. Real values only — nothing is fabricated.</CardDescription>
      </CardHeader>
      <CardContent>
        <div className="grid gap-3 sm:grid-cols-[1fr_240px]">
          <div>
            <label className="mb-1 block text-xs font-semibold text-muted-foreground">Source name (optional)</label>
            <Input value={sourceName} onChange={(e) => setSourceName(e.target.value)} placeholder="e.g. Production firewall, edge-api, audit db" className="font-mono text-sm" />
          </div>
          <div>
            <label className="mb-1 block text-xs font-semibold text-muted-foreground">Input format</label>
            <Select value={format} onValueChange={(v) => setFormat(v as LogFormat | "auto")}>
              <SelectTrigger>
                <SelectValue placeholder="Auto-detect" />
              </SelectTrigger>
              <SelectContent>
                {FORMATS.map((f) => (
                  <SelectItem key={f.value} value={f.value}>
                    {f.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>

        {!compact && mode === "upload" && (
          <div className="mt-3">
            <input ref={fileInput} type="file" className="hidden" accept=".log,.txt,.json,.cef,.evtx" onChange={(e) => onFile(e.target.files?.[0] ?? null)} />
            <button
              onClick={() => fileInput.current?.click()}
              className="flex w-full flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border bg-muted/40 px-4 py-8 text-center transition-colors hover:border-primary/40 hover:bg-muted"
            >
              <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-card text-[#2f8ce0] shadow-sm">
                <FileUp className="h-4 w-4" />
              </span>
              <span className="text-sm font-medium">{fileName ?? "Choose a log file"}</span>
              <span className="text-xs text-muted-foreground">Text read into memory, max ~20 MB — larger files: paste in chunks.</span>
            </button>
          </div>
        )}

        <Textarea
          value={content}
          onChange={(e) => setContent(e.target.value)}
          placeholder={'Paste syslog, JSON, CEF, LEEF, key=value, Apache/Nginx lines here…\n\nExample:\n<34>1 2024-01-22T12:42:48Z web1 sshd 2321 - - "Failed password for admin…"'}
          className={cn("mt-3 w-full resize-y font-mono text-xs leading-relaxed", compact ? "min-h-[130px]" : "min-h-[220px]")}
          spellCheck={false}
        />

        <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="mr-1 text-xs text-muted-foreground">Try a sample:</span>
            {SAMPLES.map((s) => (
              <button
                key={s.label}
                onClick={() => {
                  setContent(s.content);
                  setFormat("auto");
                }}
                className="rounded-full border border-border bg-card px-2.5 py-1 text-xs font-medium text-muted-foreground transition-colors hover:border-primary/50 hover:text-primary"
              >
                {s.label}
              </button>
            ))}
          </div>
          <Button onClick={submit} disabled={loading || !content.trim()} className="bg-gradient-to-r from-[#2f8ce0] to-[#7c4dcc] text-white shadow-lg shadow-[#2f8ce0]/25 hover:from-[#2a7fd1] hover:to-[#6f44bd]">
            {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
            {compact ? "Normalize" : "Normalize to OCSF"}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}