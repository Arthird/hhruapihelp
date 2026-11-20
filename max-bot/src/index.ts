import { createServer } from "node:http";
import { Bot } from "@maxhub/max-bot-api";

import { BackendClient } from "./backend.js";
import { loadConfig } from "./config.js";
import { safeHandle } from "./handler.js";

const config = loadConfig();
const backend = new BackendClient(
  config.backendBaseUrl,
  config.internalApiToken,
  config.backendTimeoutMs
);
const bot = new Bot(config.maxToken);
bot.catch((error, ctx) => {
  console.error("bot handler failed", {
    updateType: ctx.updateType,
    error: error instanceof Error ? error.message : String(error)
  });
});

const updates = [
  "message_created",
  "message_callback",
  "bot_started",
  "bot_added",
  "bot_removed",
  "dialog_cleared",
  "dialog_removed",
  "bot_stopped"
] as const;

for (const updateType of updates) {
  bot.on(updateType, async (ctx) => {
    await safeHandle(bot, ctx.update, backend);
  });
}

const health = createServer((request, response) => {
  if (request.url === "/healthz") {
    response.writeHead(200, { "Content-Type": "application/json" });
    response.end(JSON.stringify({ status: "ok" }));
    return;
  }
  response.writeHead(404);
  response.end();
});
health.listen(3000, "0.0.0.0");

let stopping = false;
async function shutdown(): Promise<void> {
  if (stopping) return;
  stopping = true;
  health.close();
  try {
    bot.stopPolling();
  } finally {
    process.exit(0);
  }
}
process.on("SIGTERM", () => void shutdown());
process.on("SIGINT", () => void shutdown());

console.log("max-bot starting in long-polling mode");
await bot.start({ mode: "polling", options: { allowedUpdates: [...updates], retry: true } });
