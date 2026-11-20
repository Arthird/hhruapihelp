export type NormalizedUpdate = {
  update_type: string;
  update_timestamp: number;
  user_locale?: string | null;
  chat: { id: number; type: "dialog" | "chat" | "channel"; title: string | null };
  user: {
    id: number;
    first_name?: string | null;
    last_name?: string | null;
    username?: string | null;
    is_bot: boolean;
  };
  message?: { id: string | null; text: string; timestamp: number | null } | null;
  callback?: { callback_id: string; payload: string; timestamp: number | null } | null;
};

export type BackendReply = {
  text: string;
  format?: "markdown" | "html";
  attachments?: Array<{
    type: "file";
    filename: string;
    mime_type: string;
    content: string;
  }>;
};

export type BackendResponse = {
  reply: BackendReply | null;
  already_delivered: boolean;
};

export function isCallbackUpdate(update: NormalizedUpdate): boolean {
  return update.update_type === "message_callback" && Boolean(update.callback);
}

export function updateKey(update: any): string {
  const type = String(update.update_type ?? "unknown");
  const timestamp = Number(update.timestamp ?? update.message?.timestamp ?? 0);
  const id =
    update.message?.body?.mid ??
    update.callback?.callback_id ??
    update.message_id ??
    update.user?.user_id ??
    update.user?.id ??
    "none";
  return `${type}:${timestamp}:${String(id)}`;
}

export function normalizeUpdate(update: any): NormalizedUpdate {
  const chat = update.chat ?? update.message?.recipient ?? {};
  const user = update.user ?? update.message?.sender ?? {};
  const body = update.message?.body ?? {};
  const chatId = Number(chat.chat_id ?? update.chat_id ?? update.message?.recipient?.chat_id);
  const userId = Number(user.user_id ?? user.id);
  if (!Number.isFinite(chatId) || chatId <= 0) {
    throw new Error("Update has no valid chat_id");
  }
  if (!Number.isFinite(userId) || userId <= 0) {
    throw new Error("Update has no valid user_id");
  }
  const chatType = String(chat.type ?? chat.chat_type ?? "dialog");
  if (!["dialog", "chat", "channel"].includes(chatType)) {
    throw new Error(`Unsupported chat type: ${chatType}`);
  }
  const normalized: NormalizedUpdate = {
    update_type: String(update.update_type),
    update_timestamp: Number(update.timestamp ?? Date.now()),
    chat: {
      id: chatId,
      type: chatType as "dialog" | "chat" | "channel",
      title: chat.title ?? null
    },
    user: {
      id: userId,
      first_name: user.first_name ?? null,
      last_name: user.last_name ?? null,
      username: user.username ?? null,
      is_bot: Boolean(user.is_bot)
    }
  };
  if (update.update_type === "message_created" || update.update_type === "message_edited") {
    normalized.message = {
      id: body.mid ?? update.message_id ?? null,
      text: String(body.text ?? ""),
      timestamp: Number(update.message?.timestamp ?? update.timestamp ?? 0)
    };
  }
  if (update.update_type === "message_callback") {
    normalized.callback = {
      callback_id: String(update.callback?.callback_id ?? ""),
      payload: String(update.callback?.payload ?? ""),
      timestamp: Number(update.callback?.timestamp ?? update.timestamp ?? 0)
    };
  }
  if (update.user_locale) {
    normalized.user_locale = String(update.user_locale);
  }
  return normalized;
}
