import { useMemo, useState } from "react";
import { toast } from "sonner";
import { Plus, Puzzle, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { PageHeader } from "@/components/common/bits";

interface CustomParser {
  id: string;
  name: string;
  keys: string[];
  description: string;
}

const PRESETS: CustomParser[] = [
  { id: "preset-nginx", name: "Nginx combined", keys: ["remote_addr", "http_user_agent", "request", "status", "bytes_sent"], description: "Two-space delimited fields of the combined access log." },
  { id: "preset-windows4625", name: "Windows Security 4625", keys: ["TargetUserName", "IpAddress", "Status", "FailureReason"], description: "Audit failure key=value block printed by Windows Event Log." },
  { id: "preset-gcp-audit", name: "GCP audit.log", keys: ["protoPayload", "resource", "timestamp", "severity"], description: "JSON-ish line keyed flat; values kept raw for deep parsing." },
  { id: "preset-k8s", name: "Kubernetes kubelet", keys: ["time", "level", "msg", "err", "ref"], description: "Structured klog lines emitted by kubelet / controller-manager." },
];

const esc = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

function extract(line: string, keys: string[]): { key: string; value: string }[] {
  const out: { key: string; value: string }[] = [];
  for (const k of keys) {
    const re = new RegExp(`${esc(k)}="((?:[^"\\\\]|\\\\.)*)"|${esc(k)}=([^\\s]+)`);
    const m = line.match(re);
    if (m) out.push({ key: k, value: m[1] ?? m[2] ?? "" });
  }
  return out;
}

export default function CustomParsers() {
  const [custom, setCustom] = useState<CustomParser[]>(PRESETS);
  const [name, setName] = useState("");
  const [keysInput, setKeysInput] = useState("");
  const [testLine, setTestLine] = useState('time="2024-01-22T12:42:48Z" level="error" msg="request failed" err="connection refused" ref="http/ingress"');
  const [testKeys, setTestKeys] = useState(["time", "level", "msg", "err", "ref"]);
  const [testOut, setTestOut] = useState<{ key: string; value: string }[] | null>(null);
  const [testParserName, setTestParserName] = useState<string | null>(null);

  const add = () => {
    const keys = keysInput.split(",").map((k) => k.trim()).filter(Boolean);
    if (!name.trim() || keys.length === 0) {
      toast.error("Give the parser a name and at least one field key");
      return;
    }
    setCustom((prev) => [...prev, { id: `custom-${Date.now()}`, name: name.trim(), keys, description: "User-defined field extractor." }]);
    setName("");
    setKeysInput("");
    toast.success("Custom parser registered");
  };

  const test = (keys: string[], parserName: string) => {
    setTestKeys(keys);
    setTestParserName(parserName);
    setTestOut(extract(testLine, keys));
  };

  const keysPreview = useMemo(() => (testOut && testOut.length > 0 ? testOut.map((t) => t.key).join(", ") : "—"), [testOut]);

  return (
    <>
      <PageHeader
        eyebrow="Parsers"
        title="Custom parsers"
        subtitle="Vendor-specific formats that don't belong in the built-in chain. Define the field keys you care about, then validate extraction against a live sample line."
      />

      <div className="grid gap-5 lg:grid-cols-2">
        <div>
          <h2 className="mb-2 text-base font-bold tracking-tight">Registered parsers</h2>
          <div className="space-y-2">
            {custom.map((p) => (
              <Card key={p.id} className="bg-card/50">
                <CardHeader className="p-4">
                  <div className="flex items-center justify-between gap-2">
                    <CardTitle className="flex items-center gap-2 text-sm">
                      <Puzzle className="h-4 w-4 text-[#7c4dcc]" />
                      {p.name}
                    </CardTitle>
                    <div className="flex items-center gap-1.5">
                      <Button variant="outline" size="sm" onClick={() => test(p.keys, p.name)}>
                        Test extraction
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-8 w-8 text-muted-foreground hover:text-rose-500"
                        onClick={() => setCustom((prev) => prev.filter((x) => x.id !== p.id))}
                        title="Remove parser"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </Button>
                    </div>
                  </div>
                  <CardDescription>{p.description}</CardDescription>
                  <div className="flex flex-wrap gap-1">
                    {p.keys.map((k) => (
                      <Badge key={k} variant="secondary" className="font-mono text-[10px]">
                        {k}
                      </Badge>
                    ))}
                  </div>
                </CardHeader>
              </Card>
            ))}
            {custom.length === 0 && <p className="rounded-lg border border-dashed border-border px-4 py-6 text-center text-sm text-muted-foreground">No custom parsers yet — create one below.</p>}
          </div>

          <Card className="mt-4">
            <CardHeader className="p-4 pb-2">
              <CardTitle className="flex items-center gap-2 text-sm">
                <Plus className="h-4 w-4 text-[#2f8ce0]" />
                Register a custom parser
              </CardTitle>
              <CardDescription>Field keys are extracted with a name=value matcher (quoted and space-separated values supported).</CardDescription>
            </CardHeader>
            <CardContent className="p-4 pt-2">
              <label className="mb-1 block text-xs font-semibold text-muted-foreground">Parser name</label>
              <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Proxylog v2" className="mb-3 font-mono text-sm" />
              <label className="mb-1 block text-xs font-semibold text-muted-foreground">Field keys (comma separated)</label>
              <Input value={keysInput} onChange={(e) => setKeysInput(e.target.value)} placeholder="time, level, msg, err" className="mb-3 font-mono text-sm" />
              <Button size="sm" onClick={add} disabled={!name.trim() || keysInput.trim().length === 0}>
                <Plus className="h-3.5 w-3.5" />
                Register
              </Button>
            </CardContent>
          </Card>
        </div>

        <div>
          <h2 className="mb-2 text-base font-bold tracking-tight">Extraction test bench</h2>
          <Card>
            <CardHeader className="p-4 pb-2">
              <div className="flex items-center justify-between gap-2">
                <CardTitle className="text-sm">Sample line</CardTitle>
                {testParserName && (
                  <Badge variant="outline" className="font-mono text-[10px]">
                    parser: {testParserName}
                  </Badge>
                )}
              </div>
              <CardDescription>Edit the sample and hit Test to re-run against the parser currently under test.</CardDescription>
            </CardHeader>
            <CardContent className="p-4 pt-2">
              <Textarea value={testLine} onChange={(e) => setTestLine(e.target.value)} className="min-h-[90px] resize-y font-mono text-xs leading-relaxed" spellCheck={false} />
              <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
                <span className="font-mono text-[10px] text-muted-foreground">expected keys: {keysPreview}</span>
                <Button variant="outline" size="sm" onClick={() => test(testKeys, testParserName ?? "last-selected")}>
                  Re-test
                </Button>
              </div>
              {testOut && (
                <div className="mt-3 overflow-hidden rounded-lg border border-border">
                  <div className="grid grid-cols-[120px_minmax(0,1fr)] gap-2 border-b border-border bg-muted/50 px-3 py-1.5 font-mono text-[10px] font-bold uppercase tracking-wider text-muted-foreground">
                    <span>key</span>
                    <span>extracted value</span>
                  </div>
                  {testOut.map((t) => (
                    <div key={t.key} className="grid grid-cols-[120px_minmax(0,1fr)] gap-2 border-b border-border bg-card px-3 py-1.5 last:border-0">
                      <span className="font-mono text-[11px] font-medium text-[#2f8ce0]">{t.key}</span>
                      <span className="break-words font-mono text-[11px] text-muted-foreground">{t.value || <em className="text-muted-foreground/60">(not present)</em>}</span>
                    </div>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </>
  );
}