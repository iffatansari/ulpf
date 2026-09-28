import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { Loader2, Play, Plus, Puzzle, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { PageHeader } from "@/components/common/bits";
import {
  createBackendParser,
  deleteBackendParser,
  listBackendParsers,
  testBackendParser,
  type BackendParserDoc,
  type BackendParserTestResult,
} from "@/lib/backend";

/**
 * Custom parsers are declarative field rules stored in the registry, not code
 * and not a browser-side regex. Testing goes through POST /parsers/test so the
 * answer comes from the server's copy of the same rules; what the browser
 * extracted on its own was never evidence the pipeline would parse anything.
 */

const DEFAULT_SAMPLE =
  'time="2024-01-22T12:42:48Z" level="error" msg="request failed" err="connection refused" ref="http/ingress"';

function slugify(name: string): string {
  return name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 64);
}

export default function CustomParsers() {
  const [parsers, setParsers] = useState<BackendParserDoc[] | null>(null);
  const [loadError, setLoadError] = useState(false);
  const [name, setName] = useState("");
  const [keysInput, setKeysInput] = useState("");
  const [sample, setSample] = useState(DEFAULT_SAMPLE);
  const [busy, setBusy] = useState(false);
  const [testingId, setTestingId] = useState<string | null>(null);
  const [result, setResult] = useState<BackendParserTestResult | null>(null);
  const [resultName, setResultName] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setParsers(await listBackendParsers(true));
      setLoadError(false);
    } catch {
      setLoadError(true);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const add = async () => {
    const keys = keysInput
      .split(",")
      .map((k) => k.trim())
      .filter(Boolean);
    if (!name.trim() || keys.length === 0) {
      toast.error("Give the parser a name and at least one field key");
      return;
    }
    const parserId = slugify(name);
    if (!parserId) {
      toast.error("That name has no usable characters for a parser id");
      return;
    }

    setBusy(true);
    try {
      const created = await createBackendParser({
        parser_id: parserId,
        display_name: name.trim(),
        description: "User-defined field extractor.",
        // A key list becomes a name=value rule, matching how the extractor is
        // meant to read a log line.
        field_rules: keys.map((k) => ({
          name: k,
          pattern: `${k}="([^"]*)"|${k}=(\\S+)`,
        })),
        source_formats: [parserId],
      });
      setName("");
      setKeysInput("");
      await load();
      toast.success(`Registered ${created.display_name}`);
    } catch {
      toast.error("Could not register that parser");
    } finally {
      setBusy(false);
    }
  };

  const remove = async (parser: BackendParserDoc) => {
    try {
      await deleteBackendParser(parser.parser_id);
      if (resultName === parser.display_name) setResult(null);
      await load();
      toast.success(`Removed ${parser.display_name}`);
    } catch {
      toast.error("Could not remove that parser");
    }
  };

  const test = async (parser: BackendParserDoc) => {
    if (!sample.trim()) {
      toast.error("Paste a sample line first");
      return;
    }
    setTestingId(parser.parser_id);
    try {
      setResult(await testBackendParser(parser.parser_id, sample));
      setResultName(parser.display_name);
    } catch {
      toast.error("Parser test failed");
    } finally {
      setTestingId(null);
    }
  };

  const custom = (parsers ?? []).filter((p) => !p.is_builtin);
  const builtins = (parsers ?? []).filter((p) => p.is_builtin);

  return (
    <>
      <PageHeader
        eyebrow="Parsers"
        title="Custom parsers"
        subtitle="Vendor-specific formats that don't belong in the built-in chain. Field keys become declarative name=value rules, and testing runs on the server against the stored rules."
      />

      {loadError ? (
        <p className="text-sm text-muted-foreground">Could not reach the parser registry.</p>
      ) : (
        <div className="grid gap-5 lg:grid-cols-2">
          <div>
            <h2 className="mb-2 text-base font-bold tracking-tight">Registered parsers</h2>
            <div className="space-y-2">
              {custom.map((p) => (
                <Card key={p.parser_id} className="bg-card/50">
                  <CardHeader className="p-4">
                    <div className="flex items-center justify-between gap-2">
                      <CardTitle className="flex items-center gap-2 text-sm">
                        <Puzzle className="h-4 w-4 text-[#7c4dcc]" />
                        {p.display_name}
                      </CardTitle>
                      <div className="flex items-center gap-1.5">
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => void test(p)}
                          disabled={testingId === p.parser_id}
                        >
                          {testingId === p.parser_id ? (
                            <Loader2 className="h-3.5 w-3.5 animate-spin" />
                          ) : (
                            <Play className="h-3.5 w-3.5" />
                          )}
                          Test
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-8 w-8 text-muted-foreground hover:text-rose-500"
                          onClick={() => void remove(p)}
                          title="Remove parser"
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </Button>
                      </div>
                    </div>
                    <CardDescription className="font-mono text-[11px]">{p.parser_id}</CardDescription>
                    <div className="flex flex-wrap gap-1">
                      {(p.field_rules ?? []).map((r) => (
                        <Badge key={r.name} variant="secondary" className="font-mono text-[10px]">
                          {r.name}
                        </Badge>
                      ))}
                    </div>
                  </CardHeader>
                </Card>
              ))}
              {!parsers && (
                <div className="flex items-center gap-2 px-1 py-2 text-sm text-muted-foreground">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  Loading…
                </div>
              )}
              {parsers && custom.length === 0 && (
                <p className="rounded-lg border border-dashed border-border px-4 py-6 text-center text-sm text-muted-foreground">
                  No custom parsers yet — create one below.
                </p>
              )}
            </div>

            {builtins.length > 0 && (
              <p className="mt-3 text-xs text-muted-foreground">
                {builtins.length} built-in parsers are also registered
                {builtins.map((b) => ` ${b.parser_id}`).join(",")}. Built-ins are
                managed on the parser configurations page and cannot be deleted.
              </p>
            )}

            <Card className="mt-4">
              <CardHeader className="p-4 pb-2">
                <CardTitle className="flex items-center gap-2 text-sm">
                  <Plus className="h-4 w-4 text-[#2f8ce0]" />
                  Register a custom parser
                </CardTitle>
                <CardDescription>
                  Field keys become name=value rules; quoted and space-separated values are both
                  matched.
                </CardDescription>
              </CardHeader>
              <CardContent className="p-4 pt-2">
                <label className="mb-1 block text-xs font-semibold text-muted-foreground">
                  Parser name
                </label>
                <Input
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="e.g. Proxylog v2"
                  className="mb-3 font-mono text-sm"
                />
                <label className="mb-1 block text-xs font-semibold text-muted-foreground">
                  Field keys (comma separated)
                </label>
                <Input
                  value={keysInput}
                  onChange={(e) => setKeysInput(e.target.value)}
                  placeholder="time, level, msg, err"
                  className="mb-3 font-mono text-sm"
                />
                <Button
                  size="sm"
                  onClick={() => void add()}
                  disabled={busy || !name.trim() || keysInput.trim().length === 0}
                >
                  {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Plus className="h-3.5 w-3.5" />}
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
                  {resultName && (
                    <Badge variant="outline" className="font-mono text-[10px]">
                      parser: {resultName}
                    </Badge>
                  )}
                </div>
                <CardDescription>
                  Pick a parser on the left to run this sample through its stored rules.
                </CardDescription>
              </CardHeader>
              <CardContent className="p-4 pt-2">
                <Textarea
                  value={sample}
                  onChange={(e) => setSample(e.target.value)}
                  className="min-h-[90px] resize-y font-mono text-xs leading-relaxed"
                  spellCheck={false}
                />
                {result && (
                  <div className="mt-3 space-y-2">
                    <p className="text-xs text-muted-foreground">
                      {result.matched ? "Matched." : "No match."}{" "}
                      {result.reason ?? ""}
                    </p>
                    {result.error && (
                      <p className="text-xs text-rose-500">Parser error: {result.error}</p>
                    )}
                    {result.extracted && Object.keys(result.extracted).length > 0 && (
                      <div className="overflow-hidden rounded-lg border border-border">
                        <div className="grid grid-cols-[120px_minmax(0,1fr)] gap-2 border-b border-border bg-muted/50 px-3 py-1.5 font-mono text-[10px] font-bold uppercase tracking-wider text-muted-foreground">
                          <span>key</span>
                          <span>extracted value</span>
                        </div>
                        {Object.entries(result.extracted).map(([key, value]) => (
                          <div
                            key={key}
                            className="grid grid-cols-[120px_minmax(0,1fr)] gap-2 border-b border-border bg-card px-3 py-1.5 last:border-0"
                          >
                            <span className="font-mono text-[11px] font-medium text-[#2f8ce0]">
                              {key}
                            </span>
                            <span className="break-words font-mono text-[11px] text-muted-foreground">
                              {value || (
                                <em className="text-muted-foreground/60">(not present)</em>
                              )}
                            </span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </CardContent>
            </Card>
          </div>
        </div>
      )}
    </>
  );
}
