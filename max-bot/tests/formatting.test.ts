import assert from "node:assert/strict";
import test from "node:test";

import { extractCodeBlocks, splitMessage } from "../src/formatting.js";

test("splitMessage keeps every code point and respects the limit", () => {
  const text = "Привет 👋 ".repeat(700);
  const parts = splitMessage(text, 4000);
  assert.ok(parts.length > 1);
  assert.ok(parts.every((part) => Array.from(part).length <= 4000));
  assert.equal(parts.join(""), text);
});

test("splitMessage does not split a surrogate pair", () => {
  const parts = splitMessage("😀".repeat(4001), 4000);
  assert.equal(parts.length, 2);
  assert.equal(parts[0], "😀".repeat(4000));
  assert.equal(parts[1], "😀");
});

test("extractCodeBlocks moves multiline code into a file", () => {
  const result = extractCodeBlocks("Пример:\n```python\nprint(1)\nprint(2)\n```\nГотово.");
  assert.equal(result.files.length, 1);
  assert.equal(result.files[0]?.filename, "example-1.py");
  assert.equal(result.files[0]?.mime_type, "text/x-python");
  assert.equal(result.files[0]?.content, "print(1)\nprint(2)");
  assert.match(result.text, /example-1\.py/);
  assert.doesNotMatch(result.text, /print\(1\)/);
});

test("extractCodeBlocks keeps one-line code inline", () => {
  const result = extractCodeBlocks("Вызовите ```GET /vacancies```.");
  assert.equal(result.files.length, 0);
  assert.match(result.text, /`GET \/vacancies`/);
});
