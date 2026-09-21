import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import { AppWindow, Cloud, Database, FileCog, HardDrive, KeyRound, Radar, Router } from "lucide-react";
import { ACCENT } from "@/lib/accents";
import type { BackendSourceDoc } from "@/lib/backend";
import { backendSourceToUi, createBackendSource, deleteBackendSource, listBackendSources, updateBackendSource } from "@/lib/backend";

export type SourceTransportId = "syslog_udp" | "syslog_tcp" | "http_collect" | "file_agent" | "custom_api";

export interface SourceTransport {
  id: SourceTransportId;
  label: string;
  hint: string;
  port?: number;
}

export interface SourceTypeDef {
  id: string;
  label: string;
  icon: LucideIcon;
  color: string;
  tint: string;
  sample: string;
  transports: SourceTransportId[];
}

export interface SourceRunStats {
  total: number;
  accepted: number;
  rejected: number;
  rescued: number;
  at: number;
}

export interface Source {
  id: string;
  name: string;
  typeId: string;
  transport: SourceTransportId;
  endpoint: string;
  format: string;
  parser_chain: string[];
  createdAt: number;
  lastRun?: SourceRunStats;
}

export interface NewSourceInput {
  name: string;
  typeId: string;
  transport: SourceTransportId;
}

export const TRANSPORTS: SourceTransport[] = [
  { id: "syslog_udp", label: "Syslog UDP", hint: "Classic device/network forwarding, port 514", port: 514 },
  { id: "syslog_tcp", label: "Syslog TCP", hint: "Reliable streaming, port 5151", port: 5151 },
  { id: "http_collect", label: "HTTP collector", hint: "POST raw batches to the ULPF ingest endpoint" },
  { id: "file_agent", label: "File / agent", hint: "Ship an existing on-disk logfile or app output" },
  { id: "custom_api", label: "Custom API", hint: "Anything that can POST JSON to /api/normalize" },
];

export const SOURCE_TYPES: SourceTypeDef[] = [
  {
    id: "firewall",
    label: "Firewall / Network",
    icon: Router,
    color: ACCENT.blue,
    tint: ACCENT.blueMist,
    transports: ["syslog_udp", "syslog_tcp", "file_agent"],
    sample: "src=203.0.113.5 dst=198.51.100.6 proto=tcp action=deny rule_name=internet-blocked",
  },
  {
    id: "application",
    label: "Application",
    icon: AppWindow,
    color: ACCENT.mint,
    tint: ACCENT.mintMist,
    transports: ["http_collect", "syslog_udp", "file_agent"],
    sample: '{"@timestamp":"2022-06-08T09:00:00.000Z","app":"order-api","msg":"order created for customer 42","user":"amy","src_ip":"10.0.0.9"}',
  },
  {
    id: "cloud",
    label: "Cloud service",
    icon: Cloud,
    color: ACCENT.lilac,
    tint: ACCENT.lilacMist,
    transports: ["http_collect", "custom_api"],
    sample: '{"@timestamp":"2022-06-08T09:00:00Z","operation":"CreateBucket","actor":"amy","source.ip":"10.0.0.1"}',
  },
  {
    id: "database",
    label: "Database",
    icon: Database,
    color: ACCENT.amber,
    tint: ACCENT.amberMist,
    transports: ["syslog_udp", "file_agent"],
    sample: 'time=2022-06-08T09:00:00Z db=orders query="SELECT * FROM users" user=amy',
  },
  {
    id: "edr",
    label: "EDR / Endpoint",
    icon: Radar,
    color: ACCENT.emerald,
    tint: ACCENT.emeraldMist,
    transports: ["file_agent", "syslog_tcp"],
    sample: '{"@timestamp":"2022-06-08T09:00:00Z","process_name":"powershell.exe","event_type":"malware detected","user":"svc-acct","threat":"Trojan.Win32.Fake","src_ip":"10.0.0.77"}',
  },
  {
    id: "iam",
    label: "IAM / Directory",
    icon: KeyRound,
    color: ACCENT.rose,
    tint: ACCENT.roseMist,
    transports: ["syslog_udp", "http_collect"],
    sample: 'time=2022-06-08T09:00:00Z user=amy action="user created" event_type=4720',
  },
  {
    id: "iot",
    label: "IoT / Hardware",
    icon: HardDrive,
    color: ACCENT.gray,
    tint: ACCENT.grayMist,
    transports: ["syslog_udp", "custom_api"],
    sample: 'time=2022-06-08T09:00:00Z device=sensor-07 message="invalid auth attempt" src_ip=10.0.0.50',
  },
  {
    id: "custom",
    label: "Custom / proprietary",
    icon: FileCog,
    color: ACCENT.gray,
    tint: ACCENT.grayMist,
    transports: ["custom_api", "http_collect"],
    sample: "line=1 ts=2022-06-08T09:00:00Z level=error message=timeout user=cron",
  },
];

const STORE_KEY = "ulpf.sources.v1";

const TYPE_TO_BACKEND: Record<string, string> = {
  firewall: "network_device",
  application: "application",
  cloud: "cloud",
  database: "database",
  edr: "server",
  iam: "application",
  iot: "iot",
  custom: "custom",
};

const TRANSPORT_TO_BACKEND: Record<string, string> = {
  syslog_udp: "udp",
  syslog_tcp: "other",
  http_collect: "http",
  file_agent: "file",
  custom_api: "other",
};

function loadStore(): Source[] {
  try {
    const raw = localStorage.getItem(STORE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as Source[];
    if (!Array.isArray(parsed)) return [];
    return parsed;
  } catch {
    return [];
  }
}

interface SourceContextValue {
  sources: Source[];
  addSource: (input: NewSourceInput) => Promise<Source>;
  removeSource: (id: string) => void;
  updateSource: (id: string, patch: Partial<Source>) => void;
  reportRun: (id: string, stats: Omit<SourceRunStats, "at">) => void;
  loadDemo: () => void;
  sourceById: (id: string) => Source | undefined;
  sourceType: (source: Source) => SourceTypeDef | undefined;
  idForName: (name: string) => string | undefined;
  refresh: () => Promise<void>;
  busy: boolean;
  backendOk: boolean;
}

const Ctx = createContext<SourceContextValue | null>(null);

function uid(): string {
  return Math.random().toString(36).slice(2, 10);
}

export function SourceRegistryProvider({ children }: { children: ReactNode }) {
  const [sources, setSources] = useState<Source[]>(loadStore);
  const [backendOk, setBackendOk] = useState(false);
  const [busy, setBusy] = useState(true);

  // Backend-primary: on mount load the real Source Registry from the API.
  // When the backend is unreachable we fall back to the local store so the
  // workspace keeps working (sources live in memory / localStorage only).
  useEffect(() => {
    let cancelled = false;
    (async () => {
      setBusy(true);
      try {
        const docs = await listBackendSources();
        if (cancelled) return;
        setSources(docs.map(backendSourceToUi));
        setBackendOk(true);
      } catch {
        if (cancelled) return;
        setBackendOk(false);
      } finally {
        if (!cancelled) setBusy(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    try {
      localStorage.setItem(STORE_KEY, JSON.stringify(sources));
    } catch {
      // storage unavailable (private mode) — sources live in memory only
    }
  }, [sources]);

  const refresh = useCallback(async () => {
    try {
      const docs = await listBackendSources();
      setSources(docs.map(backendSourceToUi));
      setBackendOk(true);
      return;
    } catch {
      setBackendOk(false);
    }
  }, []);

  const addSource = useCallback(
    async (input: NewSourceInput): Promise<Source> => {
      if (backendOk) {
        const doc = await createBackendSource({
          name: input.name.trim(),
          source_type: TYPE_TO_BACKEND[input.typeId] ?? "custom",
          transport: TRANSPORT_TO_BACKEND[input.transport] ?? "other",
          expected_format: "mixed",
          enabled: true,
        });
        const source = backendSourceToUi(doc);
        setSources((prev) => [source, ...prev]);
        return source;
      }

      const type = SOURCE_TYPES.find((t) => t.id === input.typeId);
      const transport = TRANSPORTS.find((t) => t.id === input.transport) ?? TRANSPORTS[2];
      const endpoint = transport.port !== undefined ? String(transport.port) : `/api/normalize`;
      const source: Source = {
        id: uid(),
        name: input.name.trim(),
        typeId: input.typeId,
        transport: transport.id,
        endpoint,
        format: "auto",
        parser_chain: ["Format auto-detect", "Primary chain · to be set on first import"],
        createdAt: Date.now(),
      };
      void type;
      setSources((prev) => [source, ...prev]);
      return source;
    },
    [backendOk],
  );

  const removeSource = useCallback(
    async (id: string) => {
      if (backendOk) {
        try {
          await deleteBackendSource(id);
        } catch {
          // backend already dropped it or unreachable — filter locally anyway
        }
      }
      setSources((prev) => prev.filter((s) => s.id !== id));
    },
    [backendOk],
  );

  const updateSource = useCallback(
    (id: string, patch: Partial<Source>) => {
      setSources((prev) => prev.map((s) => (s.id === id ? { ...s, ...patch } : s)));

      if (!backendOk) return;
      const apiPatch: Partial<Pick<BackendSourceDoc, "name" | "expected_format" | "enabled" | "description">> = {};
      if (patch.name !== undefined) apiPatch.name = patch.name;
      if (patch.format !== undefined) apiPatch.expected_format = patch.format;
      if (Object.keys(apiPatch).length === 0) return;

      void updateBackendSource(id, apiPatch)
        .then((doc) => {
          const source = backendSourceToUi(doc);
          setSources((prev) => prev.map((s) => (s.id === id ? { ...source, lastRun: s.lastRun } : s)));
        })
        .catch(() => {
          // local state already reflects the patch
        });
    },
    [backendOk],
  );

  const reportRun = useCallback((id: string, stats: Omit<SourceRunStats, "at">) => {
    setSources((prev) =>
      prev.map((s) => (s.id === id ? { ...s, lastRun: { ...stats, at: Date.now() } } : s)),
    );
  }, []);

  const loadDemo = useCallback(() => {
    const demos: { name: string; typeId: string; transport: SourceTransportId; endpoint: string }[] = [
      { name: "Edge firewall (PAN-OS)", typeId: "firewall", transport: "syslog_udp", endpoint: "514" },
      { name: "Order API (HTTP)", typeId: "application", transport: "http_collect", endpoint: "/api/normalize" },
      { name: "Endpoint EDR agent", typeId: "edr", transport: "file_agent", endpoint: "/var/log/edr/events.ndjson" },
    ];

    if (backendOk) {
      const existing = new Set(sources.map((s) => s.name.toLowerCase()));
      void (async () => {
        for (const d of demos) {
          if (existing.has(d.name.toLowerCase())) continue;
          try {
            await createBackendSource({
              name: d.name,
              source_type: TYPE_TO_BACKEND[d.typeId] ?? "custom",
              transport: TRANSPORT_TO_BACKEND[d.transport] ?? "other",
              expected_format: "mixed",
              enabled: true,
            });
          } catch {
            // skip sources that fail to register
          }
        }
        await refresh();
      })();
      return;
    }

    const demo: Source[] = demos.map((d) => ({
      id: uid(),
      name: d.name,
      typeId: d.typeId,
      transport: d.transport,
      endpoint: d.endpoint,
      format: "auto",
      parser_chain: ["Syslog Parser", "Key/Value Parser", "Text Parser"],
      createdAt: Date.now(),
    }));
    setSources((prev) => [...demo, ...prev]);
  }, [backendOk, refresh, sources]);

  const sourceById = useCallback((id: string) => sources.find((s) => s.id === id), [sources]);
  const sourceType = useCallback((source: Source) => SOURCE_TYPES.find((t) => t.id === source.typeId), []);
  const idForName = useCallback(
    (name: string) => sources.find((s) => s.name === name)?.id,
    [sources],
  );

  const value = useMemo(
    () => ({ sources, addSource, removeSource, updateSource, reportRun, loadDemo, sourceById, sourceType, idForName, refresh, busy, backendOk }),
    [sources, addSource, removeSource, updateSource, reportRun, loadDemo, sourceById, sourceType, idForName, refresh, busy, backendOk],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useSources(): SourceContextValue {
  const v = useContext(Ctx);
  if (!v) throw new Error("useSources must be used within SourceRegistryProvider");
  return v;
}

export type DeployMode = "agent" | "container";

export function collectorSnippet(source: Source, mode: DeployMode = "agent"): string {
  const host = "<ulpf-host:8080>";
  const quoted = JSON.stringify(source.name);
  if (mode === "container") {
    const vol = source.transport === "file_agent" ? `  -v ${source.endpoint || "/var/log/app/app.log"}:/logs:ro \\` : undefined;
    const port =
      source.transport === "syslog_udp" ? `  -p ${source.endpoint || "514"}:${source.endpoint || "514"}/udp \\` : source.transport === "syslog_tcp" ? `  -p ${source.endpoint || "5151"}:${source.endpoint || "5151"} \\` : undefined;
    return [
      `# one-click deployment: run the ULPF collector as a container (agent or agentless sidecar)`,
      `docker run -d --name ulpf-collector \\`,
      source.transport === "file_agent" ? vol : port,
      `  -e ULPF_INGEST_URL=${host}/api/normalize \\`,
      `  -e ULPF_SOURCE_NAME=${quoted} \\`,
      `  -e ULPF_TRANSPORT=${source.transport.replace("_", "-")} \\`,
      `  <ulpf-collector-image:latest>`,
    ]
      .filter((l): l is string => typeof l === "string")
      .join("\n");
  }
  switch (source.transport) {
    case "syslog_udp":
    case "syslog_tcp":
      return [
        `# ${source.transport === "syslog_udp" ? "UDP" : "TCP"} forward → ULPF ingest`,
        `*.* action(type="omfwd" target="<ulpf-host>" port="${source.endpoint || (source.transport === "syslog_udp" ? "514" : "5151")}" protocol="${source.transport === "syslog_udp" ? "udp" : "tcp"}")`,
        ``,
        `# and every line is accepted with source.name="${source.name}"`,
      ].join("\n");
    case "http_collect":
    case "custom_api":
      return [
        `# POST raw batches to the ULPF ingest endpoint (source.name tags every event)`,
        `curl -s ${host}/api/normalize -H "content-type: application/json" -d @- <<JSON`,
        `{"content":"<paste or cat a logfile>","source":{"name":${quoted}}}`,
        `JSON`,
      ].join("\n");
    case "file_agent":
      return [
        `# one-shot agent: forward an existing on-disk logfile`,
        `cat ${source.endpoint || "/var/log/app/app.log"} | jq -R -s '{content: ., source: {name: ${quoted}}}' | curl -s ${host}/api/normalize -H "content-type: application/json" --data-binary @-`,
      ].join("\n");
    default:
      return `POST ${host}/api/normalize with JSON { "content": "...", "source": { "name": ${quoted} } }`;
  }
}