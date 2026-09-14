#!/usr/bin/env node

import { execFile } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { mkdirSync, realpathSync, renameSync, unlinkSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';

const execute = promisify(execFile);
const TOOL_DIRECTORY = dirname(fileURLToPath(import.meta.url));
const WORKSPACE = dirname(TOOL_DIRECTORY);
const SCRATCH = join(WORKSPACE, '.openclaw', 'tmp');
const DESTINATION = join(SCRATCH, 'brief.evidence.json');
const SUPPORTED_SOURCES = ['microsoft365', 'slack'];
const COLLECTION_TIMEOUT_MS = 60_000;
const MAX_M365_RECORDS = 100;
const MAX_MESSAGES = 2_000;
const MAX_SLACK_USERS = 100;
const FAILURE_CODES = new Set([
  'not-configured',
  'timeout',
  'provider-failure',
  'invalid-response',
  'policy-limit',
]);

class CollectionError extends Error {
  constructor(code, message) {
    super(message);
    this.code = code;
  }
}

const collectionError = (code, message) => new CollectionError(code, message);

function unique(values, key) {
  return [...new Map(values.map((value) => [key(value), value])).values()];
}

export function validateGovernedBase(value, name = 'governed base URL') {
  if (!value) throw collectionError('not-configured', `${name} is not configured`);
  let parsed;
  try {
    parsed = new URL(value);
  } catch {
    throw collectionError('not-configured', `${name} is not a valid governed URL`);
  }
  if (!['http:', 'https:'].includes(parsed.protocol) || parsed.username || parsed.password) {
    throw collectionError('not-configured', `${name} is not a valid governed URL`);
  }
  const hostname = parsed.hostname.toLowerCase().replace(/\.+$/, '');
  if (new Set(['graph.microsoft.com', 'slack.com', 'api.slack.com']).has(hostname)) {
    throw collectionError('not-configured', `${name} must not target a public provider`);
  }
  return parsed.toString().replace(/\/$/, '');
}

async function jsonCommand(command, args, env) {
  let stdout;
  try {
    ({ stdout } = await execute(command, args, {
      env,
      maxBuffer: 16 * 1024 * 1024,
      timeout: 60_000,
    }));
  } catch (error) {
    const code = error?.killed || error?.code === 'ETIMEDOUT' ? 'timeout' : 'provider-failure';
    throw collectionError(code, 'governed Microsoft 365 command failed');
  }
  try {
    return JSON.parse(stdout);
  } catch {
    throw collectionError('invalid-response', 'governed Microsoft 365 command returned invalid JSON');
  }
}

export async function collectMicrosoft365(request) {
  const [account, inbox, events] = await Promise.all([
    request('/v1.0/me'),
    request('/v1.0/me/mailFolders/inbox/messages?$top=100'),
    request('/v1.0/me/events?$top=100'),
  ]);
  if (typeof account !== 'object' || account === null || !isNonEmptyString(account.id)) {
    throw collectionError('invalid-response', 'governed Microsoft 365 account record is invalid');
  }
  return {
    account,
    messages: boundedUniqueRecords(inbox?.value, MAX_M365_RECORDS, (item) => item?.id, 'Microsoft 365 messages'),
    events: boundedUniqueRecords(events?.value, MAX_M365_RECORDS, (item) => item?.id, 'Microsoft 365 events'),
  };
}

export async function collectMicrosoft365FromEnvironment(env, runner = jsonCommand) {
  const base = validateGovernedBase(
    env.CLIMICROSOFT365_GRAPH_BASE_URL,
    'CLIMICROSOFT365_GRAPH_BASE_URL',
  );
  return collectMicrosoft365((path) =>
    runner(
      '/sandbox/bin/m365',
      [
        'request',
        '--url',
        `${base}${path}`,
        '--resource',
        'https://graph.microsoft.com',
        '--method',
        'get',
        '--output',
        'json',
      ],
      env,
    ),
  );
}

const isNonEmptyString = (value) => typeof value === 'string' && value.trim().length > 0;

function boundedUniqueRecords(records, maximum, key, label) {
  if (!Array.isArray(records)) throw collectionError('invalid-response', `${label} response is invalid`);
  if (records.length > maximum) throw collectionError('policy-limit', `${label} result limit exceeded`);
  for (const record of records) {
    if (typeof record !== 'object' || record === null || !isNonEmptyString(key(record))) {
      throw collectionError('invalid-response', `${label} contains an invalid record`);
    }
  }
  return unique(records, key);
}

// Slack evidence is collected by the governed slack-read CLI shipped in the
// image (its `brief-evidence` command is this package's recipe: five fixed
// search terms over the last 24 hours, the newest 40 hits, their conversations
// and authors, and the containing context), the same way Microsoft 365 goes
// through /sandbox/bin/m365. The CLI owns the capability, the request budget,
// and rate-limit handling; this file only re-validates the shape it returns.
const SLACK_READ_CLI = '/sandbox/bin/slack-read';
const SLACK_EXIT_CODES = new Map([[3, 'not-configured'], [4, 'timeout'], [6, 'invalid-response']]);

async function slackCommand(args, env) {
  const command = isNonEmptyString(env.SLACK_READ_CLI) ? env.SLACK_READ_CLI : SLACK_READ_CLI;
  let stdout;
  try {
    ({ stdout } = await execute(command, args, {
      env,
      maxBuffer: 16 * 1024 * 1024,
      timeout: COLLECTION_TIMEOUT_MS + 5_000,
    }));
  } catch (error) {
    if (error?.killed || error?.code === 'ETIMEDOUT') {
      throw collectionError('timeout', 'governed Slack command timed out');
    }
    let reported;
    try {
      reported = JSON.parse(String(error?.stdout ?? ''))?.code;
    } catch {
      reported = undefined;
    }
    const code = FAILURE_CODES.has(reported)
      ? reported
      : (SLACK_EXIT_CODES.get(error?.code) ?? 'provider-failure');
    throw collectionError(code, 'governed Slack command failed');
  }
  try {
    return JSON.parse(stdout);
  } catch {
    throw collectionError('invalid-response', 'governed Slack command returned invalid JSON');
  }
}

export async function collectSlack(env, runner = slackCommand) {
  validateGovernedBase(env.SLACK_READ_BASE_URL, 'SLACK_READ_BASE_URL');
  const evidence = await runner(['brief-evidence', '--json', '--budget', String(COLLECTION_TIMEOUT_MS / 1000)], env);
  if (typeof evidence !== 'object' || evidence === null) {
    throw collectionError('invalid-response', 'governed Slack evidence is invalid');
  }
  for (const key of ['conversations', 'users', 'messages']) {
    if (!Array.isArray(evidence[key])) throw collectionError('invalid-response', 'governed Slack evidence is invalid');
  }
  if (evidence.messages.length > MAX_MESSAGES) throw collectionError('policy-limit', 'governed Slack message limit exceeded');
  if (evidence.users.length > MAX_SLACK_USERS) throw collectionError('policy-limit', 'governed Slack user limit exceeded');
  for (const message of evidence.messages) {
    if (typeof message !== 'object' || message === null || !isNonEmptyString(message.channel) || !isNonEmptyString(message.ts)) {
      throw collectionError('invalid-response', 'governed Slack evidence contains an invalid message');
    }
  }
  return {
    conversations: evidence.conversations,
    users: evidence.users,
    messages: unique(evidence.messages, (item) => `${item.channel}|${item.ts}`),
  };
}

function parseSources(args) {
  if (args.length === 0) return [...SUPPORTED_SOURCES];
  const selected = [];
  for (let index = 0; index < args.length; index += 2) {
    if (args[index] !== '--source' || index + 1 >= args.length) throw new UsageError();
    const source = args[index + 1];
    if (!SUPPORTED_SOURCES.includes(source) || selected.includes(source)) throw new UsageError();
    selected.push(source);
  }
  return selected;
}

class UsageError extends Error {}

function emptyMicrosoft365() {
  return { account: null, messages: [], events: [] };
}

function emptySlack() {
  return { conversations: [], users: [], messages: [] };
}

async function collect(args, env) {
  const requestedSources = parseSources(args);
  const attempts = await Promise.allSettled(
    requestedSources.map((source) =>
      source === 'microsoft365'
        ? collectMicrosoft365FromEnvironment(env)
        : collectSlack(env),
    ),
  );
  const unavailable = requestedSources.filter((_source, index) => attempts[index].status === 'rejected');
  const unavailableReasons = requestedSources.flatMap((source, index) => {
    const attempt = attempts[index];
    if (attempt.status !== 'rejected') return [];
    const code = FAILURE_CODES.has(attempt.reason?.code) ? attempt.reason.code : 'provider-failure';
    return [{ source, code }];
  });
  if (unavailable.length === requestedSources.length) {
    const summary = unavailableReasons.map(({ source, code }) => `${source}: ${code}`).join(', ');
    throw new Error(`no requested governed briefing source was available (${summary})`);
  }

  let microsoft365 = emptyMicrosoft365();
  let slack = emptySlack();
  requestedSources.forEach((source, index) => {
    if (attempts[index].status !== 'fulfilled') return;
    if (source === 'microsoft365') microsoft365 = attempts[index].value;
    if (source === 'slack') slack = attempts[index].value;
  });
  const manifest = {
    schemaVersion: 1,
    evidenceId: randomUUID(),
    collectedAt: new Date().toISOString(),
    requestedSources,
    unavailable,
    unavailableReasons,
    coverage: [
      { id: 'email', label: 'Email', value: String(microsoft365.messages.length) },
      { id: 'slack', label: 'Slack Messages', value: String(slack.messages.length) },
      { id: 'meetings', label: 'Meetings', value: String(microsoft365.events.length) },
      { id: 'library', label: 'Library Artifacts', value: '0' },
    ],
    microsoft365,
    slack,
  };

  mkdirSync(SCRATCH, { recursive: true, mode: 0o700 });
  const temporary = join(SCRATCH, `.brief.evidence.${process.pid}.${randomUUID()}.tmp`);
  try {
    writeFileSync(temporary, `${JSON.stringify(manifest, null, 2)}\n`, { flag: 'wx', mode: 0o600 });
    renameSync(temporary, DESTINATION);
  } finally {
    try {
      unlinkSync(temporary);
    } catch (error) {
      if (error?.code !== 'ENOENT') throw error;
    }
  }
  process.stdout.write(`collected governed briefing evidence ${manifest.evidenceId}\n`);
}

async function main() {
  try {
    await collect(process.argv.slice(2), process.env);
  } catch (error) {
    if (error instanceof UsageError) {
      process.stderr.write('usage: collect-brief-evidence.mjs [--source microsoft365] [--source slack]\n');
      process.exitCode = 2;
      return;
    }
    process.stderr.write(`brief evidence collection failed: ${error instanceof Error ? error.message : 'unknown error'}\n`);
    process.exitCode = 1;
  }
}

if (process.argv[1] && realpathSync(process.argv[1]) === realpathSync(fileURLToPath(import.meta.url))) {
  await main();
}
