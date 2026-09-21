import "dotenv/config";
import express from "express";
import cors from "cors";
import { handleBackend } from "./routes/backend";
import { handleDemo } from "./routes/demo";
import { handleNormalize } from "./routes/normalize";

export function createServer() {
  const app = express();

  // Middleware
  app.use(cors());

  // Same-origin proxy → FastAPI (must stay before the body parsers so
  // multipart uploads reach the backend untouched).
  app.use("/backend", handleBackend);

  app.use(express.json({ limit: "30mb" }));
  app.use(express.urlencoded({ extended: true }));

  // Example API routes
  app.get("/api/ping", (_req, res) => {
    const ping = process.env.PING_MESSAGE ?? "ping";
    res.json({ message: ping });
  });

  app.get("/api/demo", handleDemo);

  // OCSF log normalizer
  app.post("/api/normalize", handleNormalize);

  return app;
}
