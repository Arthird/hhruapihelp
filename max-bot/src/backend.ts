import type { BackendResponse, NormalizedUpdate } from "./update.js";

export class BackendClient {
  constructor(
    private readonly baseUrl: string,
    private readonly token: string,
    private readonly timeoutMs: number
  ) {}

  async submit(update: NormalizedUpdate, key: string): Promise<BackendResponse> {
    return this.request<BackendResponse>("/internal/v1/updates", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": key
      },
      body: JSON.stringify(update)
    });
  }

  async delivered(key: string): Promise<void> {
    await this.request(`/internal/v1/updates/${encodeURIComponent(key)}/delivered`, {
      method: "POST"
    });
  }

  async clear(chatId: number, userId: number): Promise<void> {
    const query = new URLSearchParams({ chat_id: String(chatId), user_id: String(userId) });
    await this.request(`/internal/v1/actions/clear?${query.toString()}`, { method: "POST" });
  }

  private async request<T = unknown>(path: string, init: RequestInit): Promise<T> {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), this.timeoutMs);
    try {
      let lastError: unknown;
      for (let attempt = 0; attempt < 3; attempt += 1) {
        try {
          const response = await fetch(`${this.baseUrl}${path}`, {
            ...init,
            signal: controller.signal,
            headers: {
              Authorization: `Bearer ${this.token}`,
              ...(init.headers ?? {})
            }
          });
          if (response.status >= 500 || response.status === 429) {
            throw new Error(`Backend returned ${response.status}`);
          }
          if (!response.ok) {
            const body = await response.text();
            throw new Error(`Backend returned ${response.status}: ${body.slice(0, 200)}`);
          }
          if (response.status === 204) return undefined as T;
          return (await response.json()) as T;
        } catch (error) {
          lastError = error;
          if (attempt < 2) {
            await new Promise((resolve) => setTimeout(resolve, 500 * 2 ** attempt));
          }
        }
      }
      throw lastError;
    } finally {
      clearTimeout(timeout);
    }
  }
}

