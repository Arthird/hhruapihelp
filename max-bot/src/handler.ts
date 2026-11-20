import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import type { Bot, Context } from "@maxhub/max-bot-api";

import { BackendClient } from "./backend.js";
import { extractCodeBlocks, splitMessage } from "./formatting.js";
import {
  isCallbackUpdate,
  normalizeUpdate,
  updateKey,
  type BackendReply,
  type NormalizedUpdate
} from "./update.js";

const SUPPORTED = new Set([
  "message_created",
  "message_callback",
  "bot_started",
  "bot_added",
  "bot_removed",
  "dialog_cleared",
  "dialog_removed",
  "bot_stopped"
]);

export type PendingReply = {
  update: NormalizedUpdate;
  reply: BackendReply;
  key: string;
};

export async function requestReply(
  update: any,
  backend: BackendClient
): Promise<PendingReply | null> {
  const type = String(update.update_type ?? "");
  if (!SUPPORTED.has(type)) return null;
  if (type === "message_created" && update.message?.sender?.is_bot) return null;

  let normalized: NormalizedUpdate;
  try {
    normalized = normalizeUpdate(update);
  } catch (error) {
    console.warn("update ignored", { error: String(error), type });
    return null;
  }
  const key = updateKey(update);
  const result = await backend.submit(normalized, key);
  if (!result.reply || result.already_delivered) return null;
  return { update: normalized, reply: result.reply, key };
}

export async function executeReply(
  bot: Bot<Context>,
  pending: PendingReply,
  backend: BackendClient
): Promise<void> {
  const extracted = extractCodeBlocks(pending.reply.text);
  const files = [...extracted.files, ...(pending.reply.attachments ?? [])];
  const parts = splitMessage(extracted.text);
  const callback = isCallbackUpdate(pending.update) ? pending.update.callback : null;
  if (callback) {
    await sendCallbackReply(bot, pending, extracted.text, files);
    await backend.delivered(pending.key);
    return;
  }
  const chatId = pending.update.chat.id;
  for (const part of parts) {
    await bot.api.sendMessageToChat(chatId, part, {
      format: pending.reply.format ?? "markdown"
    });
  }
  await sendFiles(bot, chatId, files);
  await backend.delivered(pending.key);
}

async function sendCallbackReply(
  bot: Bot<Context>,
  pending: PendingReply,
  text: string,
  files: Array<{ type: "file"; filename: string; mime_type: string; content: string }>
): Promise<void> {
  const callbackId = pending.update.callback?.callback_id;
  if (!callbackId) return;
  const parts = splitMessage(text);
  const first = parts.shift() ?? "";
  await bot.api.answerOnCallback(callbackId, {
    message: {
      text: first,
      format: pending.reply.format ?? "markdown"
    }
  });
  for (const part of parts) {
    await bot.api.sendMessageToChat(pending.update.chat.id, part, {
      format: pending.reply.format ?? "markdown"
    });
  }
  await sendFiles(bot, pending.update.chat.id, files);
}

async function sendFiles(
  bot: Bot<Context>,
  chatId: number,
  files: Array<{ type: "file"; filename: string; mime_type: string; content: string }>
): Promise<void> {
  if (files.length === 0) return;
  const directory = await mkdtemp(join(tmpdir(), "hh-bot-"));
  try {
    for (const file of files) {
      const path = join(directory, file.filename.replace(/[/\\]/g, "_"));
      await writeFile(path, file.content, "utf8");
      const attachment = await bot.api.uploadFile({ source: path });
      await bot.api.sendMessageToChat(chatId, "", { attachments: [attachment.toJson()] });
    }
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
}

export async function safeHandle(
  bot: Bot<Context>,
  update: any,
  backend: BackendClient
): Promise<void> {
  try {
    const pending = await requestReply(update, backend);
    if (pending) await executeReply(bot, pending, backend);
  } catch (error) {
    console.error("update handling failed", {
      type: update.update_type,
      error: error instanceof Error ? error.message : String(error)
    });
  }
}
