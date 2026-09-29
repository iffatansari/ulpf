import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import type { LucideIcon } from "lucide-react";
import {
  AppWindow,
  Cloud,
  Database,
  FileCog,
  HardDrive,
  KeyRound,
  Radar,
  Router,
} from "lucide-react";
import { ACCENT } from "@/lib/accents";
import type { BackendSourceDoc } from "@/lib/backend";
import {
  backendSourceToUi,
  createBackendSource,
  deleteBackendSource,
  listBackendSources,
  updateBackendSource,
} from "@/lib/backend";

export type SourceTransportId =
  | "syslog_udp"
  | "syslog_tcp"
  | "http_collect"
  | "sse_stream"
  | "file_agent"
  | "custom_api"
  | "kafka_sim";

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
  {
    id: "syslog_udp",
    label: "Syslog UDP",
    hint: "Classic device/network forwarding, port 1514",
    port: 1514,
  },
  {
    id: "syslog_tcp",
    label: "Syslog TCP",
    hint: "Reliable streaming, port 5151",
    port: 5151,
  },
  {
    id: "http_collect",
    label: "HTTP collector",
    hint: "POST JSON events to the collector's /logs endpoint",
  },
  {
    id: "sse_stream",
    label: "SSE stream",
    hint: "Consume a Server-Sent Events endpoint continuously",
  },
  {
    id: "file_agent",
    label: "File / agent",
    hint: "Ship an existing on-disk logfile or app output",
  },
  {
    id: "custom_api",
    label: "Custom API",
    hint: "Anything that can POST JSON to the HTTP collector",
  },
  {
    id: "kafka_sim",
    label: "Simulated live stream",
    hint: "Synthetic events generated straight into the raw topic — no upstream needed",
  },
];

export const SOURCE_TYPES: SourceTypeDef[] = [
  {
    id: "firewall",
    label: "Firewall / Network",
    icon: Router,
    color: ACCENT.blue,
    tint: ACCENT.blueMist,
    transports: ["syslog_udp", "syslog_tcp", "file_agent"],
    sample:
      "src=203.0.113.5 dst=198.51.100.6 proto=tcp action=deny rule_name=internet-blocked",
  },
  {
    id: "application",
    label: "Application",
    icon: AppWindow,
    color: ACCENT.mint,
    tint: ACCENT.mintMist,
    transports: ["http_collect", "sse_stream", "syslog_udp", "file_agent", "kafka_sim"],
    sample:
      '{"@timestamp":"2022-06-08T09:00:00.000Z","app":"order-api","msg":"order created for customer 42","user":"amy","src_ip":"10.0.0.9"}',
  },
  {
    id: "cloud",
    label: "Cloud service",
    icon: Cloud,
    color: ACCENT.lilac,
    tint: ACCENT.lilacMist,
    transports: ["http_collect", "sse_stream", "custom_api"],
    sample:
      '{"@timestamp":"2022-06-08T09:00:00Z","operation":"CreateBucket","actor":"amy","source.ip":"10.0.0.1"}',
  },
  {
    id: "database",
    label: "Database",
    icon: Database,
    color: ACCENT.amber,
    tint: ACCENT.amberMist,
    transports: ["syslog_udp", "file_agent"],
    sample:
      'time=2022-06-08T09:00:00Z db=orders query="SELECT * FROM users" user=amy',
  },
  {
    id: "edr",
    label: "EDR / Endpoint",
    icon: Radar,
    color: ACCENT.emerald,
    tint: ACCENT.emeraldMist,
    transports: ["file_agent", "syslog_tcp"],
    sample:
      '{"@timestamp":"2022-06-08T09:00:00Z","process_name":"powershell.exe","event_type":"malware detected","user":"svc-acct","threat":"Trojan.Win32.Fake","src_ip":"10.0.0.77"}',
  },
  {
    id: "iam",
    label: "IAM / Directory",
    icon: KeyRound,
    color: ACCENT.rose,
    tint: ACCENT.roseMist,
    transports: ["syslog_udp", "http_collect"],
    sample:
      'time=2022-06-08T09:00:00Z user=amy action="user created" event_type=4720',
  },
  {
    id: "iot",
    label: "IoT / Hardware",
    icon: HardDrive,
    color: ACCENT.gray,
    tint: ACCENT.grayMist,
    transports: ["syslog_udp", "custom_api"],
    sample:
      'time=2022-06-08T09:00:00Z device=sensor-07 message="invalid auth attempt" src_ip=10.0.0.50',
  },
  {
    id: "custom",
    label: "Custom / proprietary",
    icon: FileCog,
    color: ACCENT.gray,
    tint: ACCENT.grayMist,
    transports: ["custom_api", "http_collect", "sse_stream", "kafka_sim"],
    sample:
      "line=1 ts=2022-06-08T09:00:00Z level=error message=timeout user=cron",
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
  sse_stream: "sse",
  file_agent: "file",
  custom_api: "other",
  kafka_sim: "kafka_sim",
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

/**
 * Merge the registry into the current list instead of replacing it.
 *
 * A blind `docs.map(backendSourceToUi)` drops `lastRun`, which only ever lives
 * in the browser, so every refresh would wipe the "last run" acceptance stats.
 * Dropping the list wholesale is also what makes a source deleted server-side
 * linger as a ghost until a manual reload.
 */
export function reconcile(
  docs: Awaited<ReturnType<typeof listBackendSources>>,
  previous: Source[],
): Source[] {
  const prior = new Map(previous.map((source) => [source.id, source]));
  return docs.map((doc) => {
    const next = backendSourceToUi(doc);
    const lastRun = prior.get(next.id)?.lastRun;
    return lastRun ? { ...next, lastRun } : next;
  });
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
        setSources((prev) => reconcile(docs, prev));
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
      setSources((prev) => reconcile(docs, prev));
      setBackendOk(true);
      return;
    } catch {
      setBackendOk(false);
    }
  }, []);

  // The registry is the source of truth, but only the mount effect reads it.
  // A tab left open across a reset keeps a stale id, so a source created,
  // renamed or deleted on the server never shows up. Reconcile periodically
  // and again on wake, when browsers have throttled the timers.
  useEffect(() => {
    const tick = setInterval(refresh, 30000);
    const onWake = () => {
      if (document.visibilityState === "visible") refresh();
    };
    document.addEventListener("visibilitychange", onWake);
    window.addEventListener("focus", onWake);
    return () => {
      clearInterval(tick);
      document.removeEventListener("visibilitychange", onWake);
      window.removeEventListener("focus", onWake);
    };
  }, [refresh]);

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
      const transport =
        TRANSPORTS.find((t) => t.id === input.transport) ?? TRANSPORTS[2];
      const endpoint =
        transport.port !== undefined
          ? String(transport.port)
          : transport.id === "sse_stream"
            ? "https://<upstream-host>/events"
            : `/api/normalize`;
      const source: Source = {
        id: uid(),
        name: input.name.trim(),
        typeId: input.typeId,
        transport: transport.id,
        endpoint,
        format: "auto",
        parser_chain: [
          "Format auto-detect",
          "Primary chain · to be set on first import",
        ],
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
      setSources((prev) =>
        prev.map((s) => (s.id === id ? { ...s, ...patch } : s)),
      );

      if (!backendOk) return;
      const apiPatch: Partial<
        Pick<
          BackendSourceDoc,
          "name" | "expected_format" | "enabled" | "description"
        >
      > = {};
      if (patch.name !== undefined) apiPatch.name = patch.name;
      if (patch.format !== undefined) apiPatch.expected_format = patch.format;
      if (Object.keys(apiPatch).length === 0) return;

      void updateBackendSource(id, apiPatch)
        .then((doc) => {
          const source = backendSourceToUi(doc);
          setSources((prev) =>
            prev.map((s) =>
              s.id === id ? { ...source, lastRun: s.lastRun } : s,
            ),
          );
        })
        .catch(() => {
          // local state already reflects the patch
        });
    },
    [backendOk],
  );

  const reportRun = useCallback(
    (id: string, stats: Omit<SourceRunStats, "at">) => {
      setSources((prev) =>
        prev.map((s) =>
          s.id === id ? { ...s, lastRun: { ...stats, at: Date.now() } } : s,
        ),
      );
    },
    [],
  );

  const loadDemo = useCallback(() => {
    const demos: {
      name: string;
      typeId: string;
      transport: SourceTransportId;
      endpoint: string;
    }[] = [
      {
        name: "Edge firewall (PAN-OS)",
        typeId: "firewall",
        transport: "syslog_udp",
        endpoint: "1514",
      },
      {
        name: "Order API (HTTP)",
        typeId: "application",
        transport: "http_collect",
        endpoint: "http://localhost:8081/logs",
      },
      {
        name: "Endpoint EDR agent",
        typeId: "edr",
        transport: "file_agent",
        endpoint: "/var/log/edr/events.ndjson",
      },
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

  const sourceById = useCallback(
    (id: string) => sources.find((s) => s.id === id),
    [sources],
  );
  const sourceType = useCallback(
    (source: Source) => SOURCE_TYPES.find((t) => t.id === source.typeId),
    [],
  );
  const idForName = useCallback(
    (name: string) => sources.find((s) => s.name === name)?.id,
    [sources],
  );

  const value = useMemo(
    () => ({
      sources,
      addSource,
      removeSource,
      updateSource,
      reportRun,
      loadDemo,
      sourceById,
      sourceType,
      idForName,
      refresh,
      busy,
      backendOk,
    }),
    [
      sources,
      addSource,
      removeSource,
      updateSource,
      reportRun,
      loadDemo,
      sourceById,
      sourceType,
      idForName,
      refresh,
      busy,
      backendOk,
    ],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useSources(): SourceContextValue {
  const v = useContext(Ctx);
  if (!v)
    throw new Error("useSources must be used within SourceRegistryProvider");
  return v;
}

export type DeployMode = "agent" | "container";

function shellQuote(value: string): string {
  return `'${value.replace(/'/g, "'\\''")}'`;
}

function upstreamHost(url: string): string {
  try {
    return new URL(url).hostname;
  } catch {
    return "<upstream-host>";
  }
}

export function collectorSnippet(
  source: Source,
  mode: DeployMode = "agent",
): string {
  const apiHost = "<ulpf-api-host:8000>";
  const sourceId = JSON.stringify(
    source.id === "draft" ? "<source-id-assigned-after-create>" : source.id,
  );
  const sourceType = JSON.stringify(TYPE_TO_BACKEND[source.typeId] ?? "custom");
  const quoted = JSON.stringify(source.name);
  if (mode === "container") {
    if (source.transport === "kafka_sim") {
      return [
        `# set these in .env before starting the simulator`,
        `SIM_SOURCE_ID=${shellQuote(source.id === "draft" ? "<source-id-assigned-after-create>" : source.id)}`,
        `SIM_RATE=2`,
        `SIM_FORMAT=syslog,cef,json,unknown`,
        `docker compose --profile sim up --build -d live_sim`,
      ].join("\n");
    }
    if (source.transport === "sse_stream") {
      const upstreamUrl = source.endpoint || "https://<upstream-host>/events";
      return [
        `# set these in .env before starting the collector`,
        `UPSTREAM_SSE_URL=${shellQuote(upstreamUrl)}`,
        `SSE_ALLOWED_HOSTS=${shellQuote(upstreamHost(upstreamUrl))}`,
        `SSE_SOURCE_ID=${shellQuote(source.id === "draft" ? "<source-id-assigned-after-create>" : source.id)}`,
        `SSE_SOURCE_TYPE=${shellQuote(TYPE_TO_BACKEND[source.typeId] ?? "custom")}`,
        `# optional: SSE_BEARER_TOKEN (HTTPS upstreams only)`,
        `docker compose --profile sse up --build -d sse_collector`,
      ].join("\n");
    }
    if (source.transport === "file_agent") {
      return [
        `# upload a file through the ULPF API; the API invokes the file collector`,
        `curl -s -F "file=@${source.endpoint || "/var/log/app/app.log"}" ${apiHost}/sources/${source.id}/upload`,
      ].join("\n");
    }
    const port =
      source.transport === "syslog_udp"
        ? `  -p ${source.endpoint || "1514"}:${source.endpoint || "1514"}/udp \\`
        : source.transport === "syslog_tcp"
          ? `  -p ${source.endpoint || "5151"}:${source.endpoint || "5151"} \\`
          : source.transport === "http_collect" ||
              source.transport === "custom_api"
            ? `  -p 8081:8081 \\`
            : undefined;
    const sourceEnv =
      source.transport === "http_collect" || source.transport === "custom_api"
        ? "DEFAULT"
        : "SOURCE";
    return [
      `# run the ULPF collector as a container`,
      `docker run -d --name ulpf-collector \\`,
      port,
      `  -e ${sourceEnv}_ID=${sourceId} \\`,
      `  -e ${sourceEnv}_TYPE=${sourceType} \\`,
      `  -e COLLECTOR_ID=${quoted} \\`,
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
        `*.* action(type="omfwd" target="<ulpf-host>" port="${source.endpoint || (source.transport === "syslog_udp" ? "1514" : "5151")}" protocol="${source.transport === "syslog_udp" ? "udp" : "tcp"}")`,
        ``,
        `# configure the collector with SOURCE_ID=${sourceId} and SOURCE_TYPE=${sourceType}`,
      ].join("\n");
    case "http_collect":
    case "custom_api":
      return [
        `# POST events to the HTTP collector; source_id keeps the stream attributed`,
        `curl -s ${source.endpoint || "http://localhost:8081/logs"} -H "content-type: application/json" -d '{`,
        `  "source_id": ${sourceId},`,
        `  "source_type": ${sourceType},`,
        `  "events": [{"message":"hello from ${source.name}"}]`,
        `}'`,
      ].join("\n");
    case "sse_stream": {
      const upstreamUrl = source.endpoint || "https://<upstream-host>/events";
      return [
        `export UPSTREAM_SSE_URL=${shellQuote(upstreamUrl)}`,
        `export SSE_ALLOWED_HOSTS=${shellQuote(upstreamHost(upstreamUrl))}`,
        `export SSE_SOURCE_ID=${shellQuote(source.id === "draft" ? "<source-id-assigned-after-create>" : source.id)}`,
        `export SSE_SOURCE_TYPE=${shellQuote(TYPE_TO_BACKEND[source.typeId] ?? "custom")}`,
        `# optional: export SSE_BEARER_TOKEN=... (HTTPS upstreams only)`,
        `python -m collectors.sse_collector.main`,
      ].join("\n");
    }
    case "kafka_sim": {
      const resolved = source.id === "draft" ? "<source-id-assigned-after-create>" : source.id;
      return [
        `# generate a continuous synthetic stream into the raw topic`,
        `python demo/kafka_live_producer.py --source-id ${resolved} --rate 0.5`,
        ``,
        `# preview without a broker, then Ctrl-C to stop`,
        `python demo/kafka_live_producer.py --source-id ${resolved} --dry-run --count 8`,
      ].join("\n");
    }
    case "file_agent":
      return [
        `# upload a file to the API; the API streams it through the file collector`,
        `curl -s -F "file=@${source.endpoint || "/var/log/app/app.log"}" ${apiHost}/sources/${source.id}/upload`,
      ].join("\n");
    default:
      return `POST http://localhost:8081/logs with JSON { "source_id": ${sourceId}, "events": [...] }`;
  }
}
