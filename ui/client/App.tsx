import "./global.css";

import { Toaster } from "@/components/ui/toaster";
import { createRoot } from "react-dom/client";
import { Toaster as Sonner } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import AppShell from "./components/layout/AppShell";
import { NormalizerProvider } from "./lib/normalize-context";
import { SourceRegistryProvider } from "./lib/source-context";
import Dashboard from "./pages/Dashboard";
import IngestEvents from "./pages/IngestEvents";
import NormalizedEvents from "./pages/NormalizedEvents";
import Dlq from "./pages/Dlq";
import Sources from "./pages/Sources";
import AddSource from "./pages/AddSource";
import SourceDetail from "./pages/SourceDetail";
import ParserConfigurations from "./pages/ParserConfigurations";
import Drain3 from "./pages/Drain3";
import CustomParsers from "./pages/CustomParsers";
import OcsfSchema from "./pages/OcsfSchema";
import SchemaExplorer from "./pages/SchemaExplorer";
import Metrics from "./pages/Metrics";
import Logs from "./pages/Logs";
import SystemHealth from "./pages/SystemHealth";
import NotFound from "./pages/NotFound";

const queryClient = new QueryClient();

const App = () => (
  <QueryClientProvider client={queryClient}>
    <TooltipProvider>
      <NormalizerProvider>
        <SourceRegistryProvider>
          <Toaster />
          <Sonner />
          <BrowserRouter>
            <Routes>
              <Route element={<AppShell />}>
                <Route path="/" element={<Dashboard />} />
                <Route path="/events/ingest" element={<IngestEvents />} />
                <Route path="/events/normalized" element={<NormalizedEvents />} />
                <Route path="/events/dlq" element={<Dlq />} />
                <Route path="/sources" element={<Sources />} />
                <Route path="/sources/new" element={<AddSource />} />
                <Route path="/sources/:id" element={<SourceDetail />} />
                <Route path="/parsers/configurations" element={<ParserConfigurations />} />
              <Route path="/parsers/drain3" element={<Drain3 />} />
              <Route path="/parsers/custom" element={<CustomParsers />} />
              <Route path="/schema/ocsf" element={<OcsfSchema />} />
              <Route path="/schema/explorer" element={<SchemaExplorer />} />
              <Route path="/monitoring/metrics" element={<Metrics />} />
              <Route path="/monitoring/logs" element={<Logs />} />
              <Route path="/monitoring/health" element={<SystemHealth />} />
              {/* ADD ALL CUSTOM ROUTES ABOVE THE CATCH-ALL "*" ROUTE */}
            </Route>
            <Route path="*" element={<NotFound />} />
          </Routes>
        </BrowserRouter>
        </SourceRegistryProvider>
      </NormalizerProvider>
    </TooltipProvider>
  </QueryClientProvider>
);

createRoot(document.getElementById("root")!).render(<App />);