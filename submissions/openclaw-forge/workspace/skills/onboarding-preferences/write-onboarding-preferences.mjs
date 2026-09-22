#!/usr/bin/env node

import { readFile, rename, rm, writeFile } from "node:fs/promises";
import { basename, dirname, join, resolve } from "node:path";

const TOP_LEVEL_KEYS = ["schemaVersion", "updatedAt", "role", "briefSchedule", "topics"];
const ROLE_KEYS = ["id", "label"];
const SCHEDULE_KEYS = ["id", "label", "description"];
const TOPIC_KEYS = ["id", "label"];
const MAX_STRING_LENGTH = 160;

function fail(field, message) {
  throw new Error(`${field}: ${message}`);
}

function detectDuplicateKeys(source) {
  const stack = [];
  for (let index = 0; index < source.length; index += 1) {
    const character = source[index];
    if (character === "{") {
      stack.push({ type: "object", keys: new Set() });
      continue;
    }
    if (character === "[") {
      stack.push({ type: "array" });
      continue;
    }
    if (character === "}" || character === "]") {
      stack.pop();
      continue;
    }
    if (character !== '"') continue;

    const start = index;
    let escaped = false;
    index += 1;
    while (index < source.length) {
      if (escaped) escaped = false;
      else if (source[index] === "\\") escaped = true;
      else if (source[index] === '"') break;
      index += 1;
    }
    let cursor = index + 1;
    while (/\s/u.test(source[cursor] ?? "")) cursor += 1;
    const context = stack.at(-1);
    if (source[cursor] === ":" && context?.type === "object") {
      let key;
      try {
        key = JSON.parse(source.slice(start, index + 1));
      } catch {
        fail("payload", "must be valid JSON");
      }
      if (context.keys.has(key)) fail(key, "must not occur more than once");
      context.keys.add(key);
    }
  }
}

function parse(source) {
  detectDuplicateKeys(source);
  try {
    const value = JSON.parse(source);
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      fail("payload", "must be one JSON object");
    }
    return value;
  } catch (error) {
    if (error instanceof Error && error.message.includes(":")) throw error;
    fail("payload", "must be valid JSON");
  }
}

function exactObject(field, value, expectedKeys) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    fail(field, "must be an object");
  }
  const keys = Object.keys(value);
  const unknown = keys.find((key) => !expectedKeys.includes(key));
  const missing = expectedKeys.find((key) => !keys.includes(key));
  if (unknown) fail(`${field}.${unknown}`, "is not allowed");
  if (missing) fail(`${field}.${missing}`, "is required");
  if (keys.length !== expectedKeys.length) fail(field, "has an invalid shape");
}

function text(field, value) {
  if (typeof value !== "string" || value.length === 0) fail(field, "must be a non-empty string");
  if ([...value].length > MAX_STRING_LENGTH) {
    fail(field, `must be at most ${MAX_STRING_LENGTH} Unicode characters`);
  }
  if (/\p{Cc}/u.test(value)) fail(field, "must not contain control characters");
  if (value.trim() !== value) fail(field, "must not have leading or trailing whitespace");
}

function validate(profile) {
  exactObject("payload", profile, TOP_LEVEL_KEYS);
  if (profile.schemaVersion !== 1) fail("schemaVersion", "must equal 1");
  text("updatedAt", profile.updatedAt);
  if (!Number.isFinite(Date.parse(profile.updatedAt))) {
    fail("updatedAt", "must be an ISO-8601 instant");
  }

  exactObject("role", profile.role, ROLE_KEYS);
  for (const key of ROLE_KEYS) text(`role.${key}`, profile.role[key]);

  exactObject("briefSchedule", profile.briefSchedule, SCHEDULE_KEYS);
  for (const key of SCHEDULE_KEYS) text(`briefSchedule.${key}`, profile.briefSchedule[key]);

  if (!Array.isArray(profile.topics) || profile.topics.length < 1 || profile.topics.length > 24) {
    fail("topics", "must contain 1 through 24 entries");
  }
  const topicIds = new Set();
  profile.topics.forEach((topic, index) => {
    const field = `topics[${index}]`;
    exactObject(field, topic, TOPIC_KEYS);
    for (const key of TOPIC_KEYS) text(`${field}.${key}`, topic[key]);
    if (topicIds.has(topic.id)) fail(`${field}.id`, "must be unique");
    topicIds.add(topic.id);
  });
}

function canonical(profile) {
  return `${JSON.stringify(
    {
      schemaVersion: profile.schemaVersion,
      updatedAt: profile.updatedAt,
      role: { id: profile.role.id, label: profile.role.label },
      briefSchedule: {
        id: profile.briefSchedule.id,
        label: profile.briefSchedule.label,
        description: profile.briefSchedule.description,
      },
      topics: profile.topics.map((topic) => ({ id: topic.id, label: topic.label })),
    },
    null,
    2,
  )}\n`;
}

async function main() {
  const [inputArgument, outputArgument] = process.argv.slice(2);
  if (!inputArgument || !outputArgument || basename(outputArgument) !== "onboarding-preferences.json") {
    throw new Error(
      "usage: write-onboarding-preferences.mjs <input.json> onboarding-preferences.json",
    );
  }
  const inputPath = resolve(inputArgument);
  const outputPath = resolve(outputArgument);
  const profile = parse(await readFile(inputPath, "utf8"));
  validate(profile);
  const temporaryPath = join(dirname(outputPath), `.${basename(outputPath)}.${process.pid}.tmp`);
  try {
    await writeFile(temporaryPath, canonical(profile), { encoding: "utf8", mode: 0o600, flag: "wx" });
    await rename(temporaryPath, outputPath);
  } finally {
    await rm(temporaryPath, { force: true });
  }
}

main().catch((error) => {
  process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
  process.exitCode = 1;
});
