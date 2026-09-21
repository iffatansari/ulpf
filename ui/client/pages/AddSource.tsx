import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ArrowLeft, ArrowRight, Check, ChevronLeft, ChevronRight, Container, Copy, PlayCircle, Radar, Server, Sparkles, Terminal } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { PageHeader, StatChip } from "@/components/common/bits";
import { ACCENT } from "@/lib/accents";
import { collectorSnippet, SOURCE_TYPES, TRANSPORTS, useSources, type DeployMode, type Source, type SourceTransportId, type SourceTypeDef } from "@/lib/source-context";
import { useNormalizer } from "@/lib/normalize-context";
import { cn } from "@/lib/utils";

const STEPS = ["Source & transport", "Parser suggestion", "Deploy collector"];

export default function AddSource() {
  const navigate = useNavigate();
  const { addSource, reportRun } = useSources();
  const { result, loading, run } = useNormalizer();

  const [step, setStep] = useState(0);
  const [name, setName] = useState("");
  const [typeId, setTypeId] = useState("firewall");
  const [transport, setTransport] = useState<SourceTransportId>("syslog_udp");
  const [sample, setSample] = useState(SOURCE_TYPES[0].sample);
  const [deployMode, setDeployMode] = useState<DeployMode>("agent");
  const [copied, setCopied] = useState(false);

  const type = SOURCE_TYPES.find((t) => t.id === typeId) ?? SOURCE_TYPES[0];
  const selectedTransport = TRANSPORTS.find((t) => t.id === transport) ?? TRANSPORTS[0];

  const suggests: { parser_chain: string[]; format: string } = useMemo(() => {
    if (!result || !result.summary.events) return { parser_chain: [type?.label ?? "", "Key/Value Parser", "Text Parser"], format: "auto" };
    const first = result.lines.find((l) => l.ok && l.parser_chain);
    return {
      parser_chain: first && "parser_chain" in first ? first.parser_chain : [],
      format: result.summary.source_format,
    };
  }, [result, type]);

  const sourceName = name.trim() || "(untitled source)";

  const draft: Source = useMemo(
    () => ({
      id: "draft",
      name: sourceName,
      typeId,
      transport,
      endpoint: selectedTransport.port !== undefined ? String(selectedTransport.port) : "/api/normalize",
      format: suggests.format,
      parser_chain: suggests.parser_chain,
      createdAt: Date.now(),
    }),
    [sourceName, typeId, transport, selectedTransport.port, suggests],
  );

  const pickType = (t: SourceTypeDef) => {
    setTypeId(t.id);
    setSample(t.sample);
    if (!t.transports.includes(transport)) setTransport(t.transports[0]);
  };

  const runSuggestion = async () => {
    const data = await run({ content: sample, format: "auto" });
    if (data) setStep(2);
  };

  const create = async (withTest: boolean) => {
    const created = await addSource({ name, typeId, transport });
    if (withTest) {
      const sampleNow = SOURCE_TYPES.find((t) => t.id === typeId)?.sample ?? sample;
      const data = await run({ content: sampleNow, sourceName: created.name, format: "auto" });
      if (data) {
        reportRun(created.id, {
          total: data.summary.total_lines,
          accepted: data.summary.events,
          rejected: data.summary.rejected,
          rescued: data.summary.rescued,
        });
      }
    }
    navigate(`/sources/${created.id}`);
  };

  return (
    <>
      <PageHeader
        eyebrow="Sources · onboarding"
        title="Add Log Source"
        subtitle="Register the collector, let ULPF suggest the parser chain from a sample, then deploy the one-line ingest contract. Every source gets its own events, parser plan, health and DLQ."
      />

      <div className="mb-5 flex items-center gap-1.5 font-mono text-[11px]">
        {STEPS.map((label, i) => (
          <div key={label} className="flex items-center gap-1.5">
            <span
              className={cn(
                "flex items-center gap-1.5 rounded-full px-2.5 py-1 font-semibold transition-colors",
                i === step ? "text-white" : i < step ? "text-[#0f766e]" : "text-muted-foreground",
              )}
              style={i === step ? { backgroundColor: ACCENT.blue } : i < step ? { backgroundColor: ACCENT.mintMist } : undefined}
            >
              {i < step ? <Check className="h-3 w-3" /> : <span>{i + 1}</span>}
              {label}
            </span>
            {i < STEPS.length - 1 && <ArrowRight className="h-3 w-3 text-muted-foreground" />}
          </div>
        ))}
      </div>

      {step === 0 && (
        <div className="space-y-6">
          <Card className="gap-2 bg-card/60">
            <CardContent className="p-5">
              <Label className="mb-1.5 block text-xs font-semibold">Source name</Label>
              <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. edge-firewall-01, order-api, win-edr" className="max-w-md" />
              <p className="mt-1.5 font-mono text-[10px] text-muted-foreground">Events from this source are tagged metadata.log.name = “{sourceName}”.</p>
            </CardContent>
          </Card>

          <div>
            <h3 className="mb-2 text-sm font-bold tracking-tight">Source type</h3>
            <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
              {SOURCE_TYPES.map((t) => {
                const active = t.id === typeId;
                return (
                  <button key={t.id} onClick={() => pickType(t)} className={cn("relative flex flex-col items-start gap-2 rounded-xl border bg-card px-3 py-3 text-left transition-all hover:-translate-y-0.5", active ? "shadow-md" : "border-border hover:border-muted-foreground/40")} style={active ? { borderColor: t.color, boxShadow: `0 8px 16px -8px ${t.color}` } : undefined}>
                    <span className="flex h-8 w-8 items-center justify-center rounded-lg text-white" style={{ backgroundColor: t.color, boxShadow: `0 8px 14px -8px ${t.color}` }}>
                      <t.icon className="h-4 w-4" />
                    </span>
                    <span className="text-xs font-bold">{t.label}</span>
                    <span className="font-mono text-[9px] text-muted-foreground">{t.transports.length} transports</span>
                    {active && <span className="absolute right-2 top-2 flex h-4 w-4 items-center justify-center rounded-full text-white" style={{ backgroundColor: t.color }}><Check className="h-2.5 w-2.5" /></span>}
                  </button>
                );
              })}
            </div>
          </div>

          <div>
            <h3 className="mb-2 text-sm font-bold tracking-tight">Transport</h3>
            <div className="grid gap-2 sm:grid-cols-3">
              {TRANSPORTS.map((t) => {
                const active = t.id === transport;
                const disabled = !type.transports.includes(t.id);
                return (
                  <button key={t.id} onClick={() => setTransport(t.id)} disabled={disabled} className={cn("rounded-xl border bg-card px-3 py-2.5 text-left transition-all", disabled ? "cursor-not-allowed opacity-40" : "hover:-translate-y-0.5", active ? "shadow-md" : "border-border")} style={active ? { borderColor: ACCENT.blue, boxShadow: `0 8px 16px -8px ${ACCENT.blue}` } : undefined}>
                    <span className="block text-xs font-bold">{t.label}</span>
                    <span className="mt-0.5 block text-[10px] leading-snug text-muted-foreground">{t.hint}</span>
                  </button>
                );
              })}
            </div>
          </div>
        </div>
      )}

      {step === 1 && (
        <div className="space-y-5">
          <Card className="gap-2 bg-card/60">
            <CardContent className="p-5">
              <div className="mb-2 flex items-center justify-between gap-2">
                <Label className="text-xs font-semibold">Sample logs</Label>
                <Button variant="ghost" size="sm" className="h-7 text-xs" onClick={() => setSample(type.sample)}>
                  <Sparkles className="h-3 w-3" />
                  Reset sample
                </Button>
              </div>
              <Textarea value={sample} onChange={(e) => setSample(e.target.value)} rows={6} className="font-mono text-xs" placeholder="Paste real sample logs from this source…" />
              <p className="mt-1.5 text-[11px] text-muted-foreground">ULPF detects the format and suggests the parser chain + schema mapping by actually normalizing this sample.</p>
            </CardContent>
          </Card>

          {result && (
            <div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-4">
              <StatChip icon={Server} label="Detected" value={result.summary.source_format} color={ACCENT.blue} tone="text-[#25263A]" />
              <StatChip icon={Check} label="Would produce" value={result.summary.events} color="#2f8ce0" tone="text-emerald-600" />
              <StatChip icon={Radar} label="Rescued by fallback" value={result.summary.rescued} color="#7c4dcc" tone={result.summary.rescued ? "text-[#25263A]" : "text-[#A6AABF]"} />
              <StatChip icon={PlayCircle} label="DLQ lines" value={result.summary.rejected} color="#e11d48" tone={result.summary.rejected ? "text-rose-600" : "text-[#A6AABF]"} />
            </div>
          )}

          <div className="rounded-xl border border-border bg-card p-5">
            <h3 className="mb-2 text-sm font-bold tracking-tight">Generated parser template</h3>
            <div className="flex flex-wrap items-center gap-1.5">
              {suggests.parser_chain.length > 0 ? (
                suggests.parser_chain.map((p, i) => (
                  <span key={p} className="flex items-center gap-1.5">
                    <Badge className={cn("px-2 py-1 font-mono text-[11px] font-semibold", i === 0 && "border-transparent bg-[#E8F4FF] text-[#2f8ce0]", i > 0 && "border-transparent bg-[#F0ECFF] text-[#7c4dcc]")}>
                      {i === 0 && "primary · "}
                      {p}
                    </Badge>
                    {i < suggests.parser_chain.length - 1 && <ArrowRight className="h-3 w-3 text-muted-foreground" />}
                  </span>
                ))
              ) : (
                <span className="text-sm text-muted-foreground">Run the detection to see the chain the engine will try.</span>
              )}
            </div>
            {result && result.summary.events > 0 && (
              <div className="mt-3 flex flex-wrap items-center gap-1.5">
                <span className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">schema mapping</span>
                {Object.entries(result.summary.by_class).map(([cls, count]) => (
                  <Badge key={cls} variant="secondary" className="font-mono text-[11px]">
                    {cls} · {count}
                  </Badge>
                ))}
              </div>
            )}
          </div>
          <p className="font-mono text-[10px] text-muted-foreground">
            The chain is the full multi-parser fallback plan: ULPF tries the primary, then generic parsers, and only lines all parsers reject reach the DLQ with a “parsers tried” audit trail.
          </p>
        </div>
      )}

      {step === 2 && (
        <div className="space-y-5">
          <div className="overflow-hidden rounded-xl border border-border">
            <div className="flex items-center justify-between gap-2 border-b border-border bg-muted/40 px-4 py-2">
              <div className="flex items-center gap-1.5">
                <span className="flex h-6 w-6 items-center justify-center rounded-md text-white" style={{ backgroundColor: type.color }}>
                  <type.icon className="h-3 w-3" />
                </span>
                <span className="text-sm font-bold">{sourceName}</span>
                <Badge variant="outline" className="font-mono text-[10px]">{type.label}</Badge>
                <Badge variant="outline" className="font-mono text-[10px]">{selectedTransport.label}</Badge>
              </div>
              <span className="inline-flex items-center gap-1 rounded-full bg-[#BDEDE3] px-2 py-0.5 text-[10px] font-bold text-[#0f766e]">
                <span className="h-1.5 w-1.5 rounded-full bg-[#0f766e]" />
                Ready
              </span>
            </div>
<div className="p-4">
              <p className="mb-3 text-xs text-muted-foreground">One-click deployment — pick agent or container. Every event is tagged with this source's name.</p>
              <div className="mb-3 flex w-full items-center gap-2 rounded-lg border border-border bg-muted/40 p-1">
                <button
                  type="button"
                  onClick={() => { setDeployMode("agent"); setCopied(false); }}
                  className={cn(
                    "flex flex-1 items-center justify-center gap-1.5 rounded-md px-2 py-1 text-xs font-semibold transition-all",
                    deployMode === "agent" ? "bg-white text-[#25263A] shadow-sm" : "text-muted-foreground hover:bg-white/60",
                  )}
                >
                  <Terminal className="h-3 w-3" />
                  Agent
                </button>
                <button
                  type="button"
                  onClick={() => { setDeployMode("container"); setCopied(false); }}
                  className={cn(
                    "flex flex-1 items-center justify-center gap-1.5 rounded-md px-2 py-1 text-xs font-semibold transition-all",
                    deployMode === "container" ? "bg-white text-[#25263A] shadow-sm" : "text-muted-foreground hover:bg-white/60",
                  )}
                >
                  <Container className="h-3 w-3" />
                  Container
                </button>
              </div>
              <div className="relative">
                <pre className="overflow-x-auto rounded-lg bg-[#25263A] p-3 font-mono text-[11px] leading-relaxed text-[#E8F4FF]">{collectorSnippet(draft, deployMode)}</pre>
                <button
                  type="button"
                  onClick={async () => {
                    try {
                      await navigator.clipboard.writeText(collectorSnippet(draft, deployMode));
                      setCopied(true);
                      setTimeout(() => setCopied(false), 1500);
                    } catch {
                      // clipboard unavailable — ignore
                    }
                  }}
                  className="absolute right-2 top-2 inline-flex items-center gap-1 rounded-md border border-white/20 bg-white/10 px-2 py-1 font-mono text-[10px] text-white/90 backdrop-blur transition-colors hover:bg-white/20"
                >
                  {copied ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
                  {copied ? "Copied" : "Copy"}
                </button>
              </div>
              <div className="mt-3 flex flex-wrap items-center gap-1.5">
                <span className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">parser plan</span>
                {suggests.parser_chain.map((p, i) => (
                  <Badge key={p} className={cn("px-1.5 py-0.5 font-mono text-[10px]", i === 0 && "border-transparent bg-[#E8F4FF] text-[#2f8ce0]", i > 0 && "border-transparent bg-[#F0ECFF] text-[#7c4dcc]")}>
                    {p}
                  </Badge>
                ))}
</div>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2.5">
            <Button onClick={() => create(true)} className="bg-gradient-to-r from-[#2f8ce0] to-[#7c4dcc] text-white shadow-lg shadow-[#2f8ce0]/25" disabled={!name.trim()}>
              <Sparkles className="h-4 w-4" />
              Create & test with sample
            </Button>
            <Button variant="outline" onClick={() => create(false)} disabled={!name.trim()}>
              Create source
            </Button>
          </div>
          {!name.trim() && <p className="text-xs text-muted-foreground">Name the source first so events can be tagged and a dashboard created.</p>}
        </div>
      )}

      <div className="mt-8 flex items-center justify-between">
        <Button variant="outline" size="sm" onClick={() => (step === 0 ? navigate("/sources") : setStep(step - 1))} disabled={loading}>
          <ChevronLeft className="h-3.5 w-3.5" />
          {step === 0 ? "Cancel" : "Back"}
        </Button>
        {step < 2 ? (
          <Button size="sm" onClick={async () => (step === 0 ? setStep(1) : await runSuggestion())} disabled={loading}>
            {loading ? <PlayCircle className="h-3.5 w-3.5 animate-spin" /> : <ChevronRight className="h-3.5 w-3.5" />}
            {step === 0 ? "Next" : loading ? "Detecting…" : "Detect format & suggest parsers"}
          </Button>
        ) : (
          <Button asChild variant="ghost" size="sm">
            <Link to="/sources">
              Sources <ArrowLeft className="h-3.5 w-3.5" />
            </Link>
          </Button>
        )}
      </div>
    </>
  );
}