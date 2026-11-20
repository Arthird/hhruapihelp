import { readFileSync } from "node:fs";
import { config as loadDotenv } from "dotenv";
import { z } from "zod";

loadDotenv({ path: "../.env", override: false });
loadDotenv({ override: false });

const EnvSchema = z.object({
  MAX_BOT_API_KEY: z.string().min(1),
  MAX_API_BASE_URL: z.string().url().default("https://platform-api2.max.ru"),
  MAX_ROOT_CA_PATH: z.string().min(1),
  BACKEND_BASE_URL: z.string().url(),
  INTERNAL_API_TOKEN: z.string().min(16),
  BACKEND_TIMEOUT_SECONDS: z.coerce.number().positive().default(60),
  LOG_LEVEL: z.string().default("INFO")
});

export type Config = {
  maxToken: string;
  maxBaseUrl: string;
  maxRootCaPath: string;
  backendBaseUrl: string;
  internalApiToken: string;
  backendTimeoutMs: number;
  logLevel: string;
};

export function loadConfig(env: NodeJS.ProcessEnv = process.env): Config {
  const parsed = EnvSchema.safeParse(env);
  if (!parsed.success) {
    const keys = parsed.error.issues.map((issue) => issue.path.join(".")).join(", ");
    throw new Error(`Invalid environment configuration: ${keys}`);
  }
  const data = parsed.data;
  if (data.MAX_API_BASE_URL !== "https://platform-api2.max.ru") {
    throw new Error("MAX_API_BASE_URL must be https://platform-api2.max.ru");
  }
  readFileSync(data.MAX_ROOT_CA_PATH);
  return {
    maxToken: data.MAX_BOT_API_KEY,
    maxBaseUrl: data.MAX_API_BASE_URL,
    maxRootCaPath: data.MAX_ROOT_CA_PATH,
    backendBaseUrl: data.BACKEND_BASE_URL.replace(/\/$/, ""),
    internalApiToken: data.INTERNAL_API_TOKEN,
    backendTimeoutMs: data.BACKEND_TIMEOUT_SECONDS * 1000,
    logLevel: data.LOG_LEVEL
  };
}
