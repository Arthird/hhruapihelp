import assert from "node:assert/strict";
import test from "node:test";

import { normalizeUpdate, updateKey } from "../src/update.js";

const update = {
  update_type: "message_created",
  timestamp: 1760000000000,
  chat: { chat_id: 10, type: "dialog", title: null },
  user: { user_id: 20, first_name: "Иван", is_bot: false },
  message: {
    sender: { user_id: 20, first_name: "Иван", is_bot: false },
    recipient: { chat_id: 10, chat_type: "dialog" },
    timestamp: 1760000000000,
    body: { mid: "mid-1", text: "Как получить вакансии?" }
  }
};

test("normalizeUpdate maps MAX payload to the backend contract", () => {
  const result = normalizeUpdate(update);
  assert.equal(result.update_type, "message_created");
  assert.equal(result.chat.id, 10);
  assert.equal(result.chat.type, "dialog");
  assert.equal(result.user.id, 20);
  assert.equal(result.user.is_bot, false);
  assert.deepEqual(result.message, {
    id: "mid-1",
    text: "Как получить вакансии?",
    timestamp: 1760000000000
  });
});

test("updateKey is stable for duplicate polling deliveries", () => {
  assert.equal(updateKey(update), "message_created:1760000000000:mid-1");
  assert.equal(updateKey({ ...update }), updateKey(update));
});

test("normalizeUpdate rejects update without chat", () => {
  assert.throws(() => normalizeUpdate({ update_type: "message_created", user: { user_id: 1 } }));
});
