export function splitMessage(text: string, limit = 4000): string[] {
  if (limit < 1) throw new RangeError("limit must be positive");
  let rest = Array.from(text);
  const parts: string[] = [];
  while (rest.length > limit) {
    let cut = rest.lastIndexOf("\n", limit - 1);
    if (cut < limit / 2) cut = rest.lastIndexOf(" ", limit - 1);
    if (cut < limit / 2) cut = limit;
    const part = rest.slice(0, cut).join("");
    parts.push(part);
    rest = rest.slice(cut);
  }
  parts.push(rest.join(""));
  return parts;
}

const CODE_BLOCK_RE = /```([a-zA-Z0-9_+-]*)\n([\s\S]*?)```/g;

export function extractCodeBlocks(text: string): {
  text: string;
  files: Array<{ type: "file"; filename: string; mime_type: string; content: string }>;
} {
  const files: Array<{ type: "file"; filename: string; mime_type: string; content: string }> = [];
  let index = 0;
  const cleaned = text.replace(CODE_BLOCK_RE, (_match, language: string, code: string) => {
    if (!code.includes("\n")) return `\`${code.trim()}\``;
    index += 1;
    const extension = extensionFor(language);
    files.push({
      type: "file",
      filename: `example-${index}.${extension}`,
      mime_type: mimeFor(extension),
      content: code.replace(/\n$/, "")
    });
    return `\n[Пример кода ${index} во вложении: example-${index}.${extension}]\n`;
  });
  return { text: cleaned.replace(/\n{3,}/g, "\n\n").trim(), files };
}

function extensionFor(language: string): string {
  const normalized = language.toLowerCase();
  const map: Record<string, string> = {
    python: "py",
    py: "py",
    javascript: "js",
    js: "js",
    typescript: "ts",
    ts: "ts",
    json: "json",
    bash: "sh",
    shell: "sh",
    sh: "sh",
    http: "http",
    sql: "sql",
    java: "java",
    go: "go",
    ruby: "rb",
    php: "php"
  };
  return map[normalized] ?? "txt";
}

function mimeFor(extension: string): string {
  const map: Record<string, string> = {
    py: "text/x-python",
    js: "text/javascript",
    ts: "text/typescript",
    json: "application/json",
    sh: "text/x-shellscript",
    http: "text/plain",
    sql: "application/sql",
    java: "text/x-java-source",
    go: "text/x-go",
    rb: "text/x-ruby",
    php: "text/x-php",
    txt: "text/plain"
  };
  return map[extension] ?? "text/plain";
}

export function sanitizeMarkdown(text: string): string {
  return text.replace(/([\\`*_{}[\]()#+.!|>-])/g, "\\$1");
}
