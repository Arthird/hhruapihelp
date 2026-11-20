import assert from "node:assert/strict";
import { createServer } from "node:http";
import type { AddressInfo } from "node:net";
import test from "node:test";

import { BackendClient } from "../src/backend.js";

test("BackendClient sends auth and idempotency headers", async () => {
  let received: Record<string, string | string[] | undefined> = {};
  const server = createServer((request, response) => {
    received = request.headers;
    response.writeHead(200, { "Content-Type": "application/json" });
    response.end(JSON.stringify({ reply: null, already_delivered: false }));
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const port = (server.address() as AddressInfo).port;
  try {
    const client = new BackendClient(`http://127.0.0.1:${port}`, "token-1234567890123456", 2000);
    await client.submit(
      {
        update_type: "message_created",
        update_timestamp: 1,
        chat: { id: 1, type: "dialog", title: null },
        user: { id: 2, is_bot: false }
      },
      "message_created:1:mid-1"
    );
    assert.equal(received.authorization, "Bearer token-1234567890123456");
    assert.equal(received["idempotency-key"], "message_created:1:mid-1");
  } finally {
    await new Promise<void>((resolve) => server.close(() => resolve()));
  }
});
