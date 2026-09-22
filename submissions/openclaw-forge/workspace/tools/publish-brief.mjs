#!/usr/bin/env node

import { randomUUID } from 'node:crypto';
import {
  lstatSync,
  readFileSync,
  readdirSync,
  realpathSync,
  renameSync,
  unlinkSync,
  writeFileSync,
} from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const TOOL_DIRECTORY = dirname(fileURLToPath(import.meta.url));
const WORKSPACE = dirname(TOOL_DIRECTORY);
const CANDIDATE = join(WORKSPACE, '.openclaw', 'tmp', 'brief.candidate.json');
const EVIDENCE = join(WORKSPACE, '.openclaw', 'tmp', 'brief.evidence.json');
const DESTINATION = join(WORKSPACE, 'brief.json');
const BRIEF_SOURCES = new Set(['email', 'slack', 'calendar']);
const EVIDENCE_SOURCES = new Set(['microsoft365', 'slack']);
const FAILURE_CODES = new Set([
  'not-configured',
  'timeout',
  'provider-failure',
  'invalid-response',
  'policy-limit',
]);
const MAX_EVIDENCE_AGE_MS = 15 * 60_000;
const MAX_FUTURE_SKEW_MS = 5 * 60_000;
const STALE_TEMP_AGE_MS = 15 * 60_000;
const OWNED_TEMP_NAME =
  /^\.brief\.(?:json|rollback)\.\d+\.[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\.tmp$/i;
const COVERAGE = [
  { id: 'email', label: 'Email' },
  { id: 'slack', label: 'Slack Messages' },
  { id: 'meetings', label: 'Meetings' },
  { id: 'library', label: 'Library Artifacts' },
];

const isObject = (value) => typeof value === 'object' && value !== null && !Array.isArray(value);
const isNonEmptyString = (value) => typeof value === 'string' && value.trim().length > 0;
const isIsoInstant = (value) => {
  if (typeof value !== 'string') return false;
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(?:Z|[+-](\d{2}):(\d{2}))$/.exec(value);
  if (!match || !Number.isFinite(Date.parse(value))) return false;
  const [, year, month, day, hour, minute, second, offsetHour = '00', offsetMinute = '00'] = match;
  const numeric = [year, month, day, hour, minute, second, offsetHour, offsetMinute].map(Number);
  const [y, m, d, h, min, sec, oh, om] = numeric;
  const daysInMonth = new Date(Date.UTC(y, m, 0)).getUTCDate();
  return m >= 1 && m <= 12 && d >= 1 && d <= daysInMonth && h <= 23 && min <= 59 && sec <= 59 && oh <= 23 && om <= 59;
};

function requireString(value, field, path, issues, nonEmpty = false) {
  if (typeof value?.[field] !== 'string' || (nonEmpty && value[field].trim().length === 0)) {
    issues.push(`${path}.${field} must be ${nonEmpty ? 'a non-empty' : 'a'} string`);
  }
}

function validateCoverage(value, issues, path = 'coverage') {
  if (!Array.isArray(value)) {
    issues.push(`${path} must be an array`);
    return;
  }
  if (value.length !== COVERAGE.length) issues.push(`${path} must contain exactly four rows`);
  COVERAGE.forEach((expected, index) => {
    const row = value[index];
    if (!isObject(row)) {
      issues.push(`${path}[${index}] must be an object`);
      return;
    }
    if (row.id !== expected.id || row.label !== expected.label) {
      issues.push(`${path}[${index}] must be ${expected.id}/${expected.label}`);
    }
    if (typeof row.value !== 'string' || !/^\d+$/.test(row.value)) {
      issues.push(`${path}[${index}].value must be a decimal string`);
    }
  });
  if (value[3]?.value !== '0') issues.push(`${path}[3].value must be 0`);
}

function validateUniqueIds(section, name, issues) {
  if (!Array.isArray(section)) {
    issues.push(`${name} must be an array`);
    return;
  }
  const ids = new Set();
  section.forEach((entry, index) => {
    if (!isObject(entry)) {
      issues.push(`${name}[${index}] must be an object`);
      return;
    }
    if (!isNonEmptyString(entry.id)) issues.push(`${name}[${index}].id must be a non-empty string`);
    else if (ids.has(entry.id)) issues.push(`${name}[${index}].id must be unique within ${name}`);
    else ids.add(entry.id);
  });
}

function validateNote(note, section, index, issues) {
  if (!isObject(note)) return;
  if (!BRIEF_SOURCES.has(note.source)) issues.push(`${section}[${index}].source is invalid`);
  requireString(note, 'meta', `${section}[${index}]`, issues);
  requireString(note, 'body', `${section}[${index}]`, issues);
  if (note.timing !== undefined && typeof note.timing !== 'string') {
    issues.push(`${section}[${index}].timing must be a string`);
  }
  if (note.whenAt !== undefined && !isIsoInstant(note.whenAt)) {
    issues.push(`${section}[${index}].whenAt must be an ISO-8601 instant`);
  }
  if (note.highlight !== undefined) {
    if (typeof note.highlight !== 'string') issues.push(`${section}[${index}].highlight must be a string`);
    else if (typeof note.body === 'string' && !note.body.includes(note.highlight)) {
      issues.push(`${section}[${index}].highlight must be an exact substring of body`);
    }
  }
}

export function validateBriefCandidate(value) {
  const issues = [];
  if (!isObject(value)) return ['brief candidate must be an object'];
  if (value.schemaVersion !== 1) issues.push('schemaVersion must be 1');
  if (!isNonEmptyString(value.evidenceId)) issues.push('evidenceId must be a non-empty string');
  if (!isIsoInstant(value.generatedAt)) issues.push('generatedAt must be an ISO-8601 instant');
  requireString(value, 'generatedFor', 'brief', issues, true);

  if (!isObject(value.greeting)) issues.push('greeting must be an object');
  else {
    for (const field of ['name', 'role', 'initials', 'date']) {
      requireString(value.greeting, field, 'greeting', issues);
    }
    if (!isObject(value.greeting.summary)) issues.push('greeting.summary must be an object');
    else {
      requireString(value.greeting.summary, 'lead', 'greeting.summary', issues);
      requireString(value.greeting.summary, 'tail', 'greeting.summary', issues);
      if (
        value.greeting.summary.highlight !== undefined &&
        typeof value.greeting.summary.highlight !== 'string'
      ) issues.push('greeting.summary.highlight must be a string');
    }
  }

  for (const section of ['notifications', 'coverage', 'topOfMind', 'fyi', 'lookingAhead']) {
    validateUniqueIds(value[section], section, issues);
  }
  validateCoverage(value.coverage, issues);

  let proposalCount = 0;
  if (Array.isArray(value.topOfMind)) {
    value.topOfMind.forEach((item, index) => {
      if (!isObject(item)) return;
      if (!BRIEF_SOURCES.has(item.source)) issues.push(`topOfMind[${index}].source is invalid`);
      for (const field of ['meta', 'title', 'description']) {
        requireString(item, field, `topOfMind[${index}]`, issues);
      }
      if (item.draftId === undefined && item.actions === undefined) return;
      if (!isNonEmptyString(item.draftId) || !Array.isArray(item.actions)) {
        issues.push(`topOfMind[${index}] draftId and actions must be a non-empty pair`);
        return;
      }
      const canonical =
        item.actions.length === 2 &&
        isObject(item.actions[0]) &&
        item.actions[0].label === 'Review draft' &&
        item.actions[0].isPrimary === true &&
        isObject(item.actions[1]) &&
        item.actions[1].label === 'Dismiss' &&
        item.actions[1].isPrimary === undefined;
      if (!canonical) issues.push(`topOfMind[${index}].actions must be the canonical pair`);
      else proposalCount += 1;
    });
  }

  const notifications = value.notifications;
  if (Array.isArray(notifications)) {
    if (proposalCount === 0 && notifications.length !== 0) {
      issues.push('notifications must be empty without proposal-backed items');
    } else if (proposalCount > 0 && notifications.length !== 1) {
      issues.push('one drafts notification is required');
    }
    if (notifications.length > 1) issues.push('notifications may contain at most one item');
    const notification = notifications[0];
    if (notification !== undefined && isObject(notification)) {
      if (notification.id !== 'drafts' || notification.to !== '/messages') {
        issues.push('draft notification must use drafts and /messages');
      }
      if (notification.label !== `${proposalCount} drafts need review`) {
        issues.push('draft notification count must match proposal-backed items');
      }
    }
  }

  if (Array.isArray(value.fyi)) value.fyi.forEach((note, index) => validateNote(note, 'fyi', index, issues));
  if (Array.isArray(value.lookingAhead)) {
    value.lookingAhead.forEach((note, index) => validateNote(note, 'lookingAhead', index, issues));
  }
  return issues;
}

function validateSourceList(value, field, issues) {
  if (!Array.isArray(value[field])) {
    issues.push(`${field} must be an array`);
    return;
  }
  const seen = new Set();
  for (const source of value[field]) {
    if (!EVIDENCE_SOURCES.has(source)) issues.push(`${field} contains unsupported source`);
    if (seen.has(source)) issues.push(`${field} must not contain duplicates`);
    seen.add(source);
  }
}

export function validateBriefEvidence(value) {
  const issues = [];
  if (!isObject(value)) return ['brief evidence must be an object'];
  if (value.schemaVersion !== 1) issues.push('schemaVersion must be 1');
  if (!isNonEmptyString(value.evidenceId)) issues.push('evidenceId must be a non-empty string');
  if (!isIsoInstant(value.collectedAt)) issues.push('collectedAt must be an ISO-8601 instant');
  validateSourceList(value, 'requestedSources', issues);
  validateSourceList(value, 'unavailable', issues);
  if (!Array.isArray(value.unavailableReasons)) {
    issues.push('unavailableReasons must be an array');
  } else {
    const reasonSources = new Set();
    value.unavailableReasons.forEach((reason, index) => {
      if (!isObject(reason)) {
        issues.push(`unavailableReasons[${index}] must be an object`);
        return;
      }
      if (!EVIDENCE_SOURCES.has(reason.source)) {
        issues.push(`unavailableReasons[${index}].source is unsupported`);
      } else if (reasonSources.has(reason.source)) {
        issues.push('unavailableReasons must not contain duplicate sources');
      } else {
        reasonSources.add(reason.source);
      }
      if (!FAILURE_CODES.has(reason.code)) {
        issues.push(`unavailableReasons[${index}].code is unsupported`);
      }
    });
    if (
      Array.isArray(value.unavailable) &&
      JSON.stringify([...reasonSources]) !== JSON.stringify(value.unavailable)
    ) {
      issues.push('unavailableReasons must correspond exactly to unavailable sources');
    }
  }
  if (Array.isArray(value.requestedSources) && Array.isArray(value.unavailable)) {
    if (value.requestedSources.length === 0) issues.push('requestedSources must not be empty');
    for (const source of value.unavailable) {
      if (!value.requestedSources.includes(source)) issues.push('unavailable source must be requested');
    }
    if (value.requestedSources.length > 0 && value.unavailable.length === value.requestedSources.length) {
      issues.push('at least one requested source must be available');
    }
  }
  validateCoverage(value.coverage, issues, 'evidence.coverage');
  if (!isObject(value.microsoft365)) issues.push('microsoft365 must be an object');
  else {
    if (!Array.isArray(value.microsoft365.messages)) issues.push('microsoft365.messages must be an array');
    if (!Array.isArray(value.microsoft365.events)) issues.push('microsoft365.events must be an array');
  }
  if (!isObject(value.slack)) issues.push('slack must be an object');
  else {
    for (const field of ['conversations', 'users', 'messages']) {
      if (!Array.isArray(value.slack[field])) issues.push(`slack.${field} must be an array`);
    }
  }
  const microsoftAvailable =
    Array.isArray(value.requestedSources) && value.requestedSources.includes('microsoft365') &&
    Array.isArray(value.unavailable) && !value.unavailable.includes('microsoft365');
  const slackAvailable =
    Array.isArray(value.requestedSources) && value.requestedSources.includes('slack') &&
    Array.isArray(value.unavailable) && !value.unavailable.includes('slack');

  if (isObject(value.microsoft365)) {
    if (microsoftAvailable) {
      if (!isObject(value.microsoft365.account) || !isNonEmptyString(value.microsoft365.account.id)) {
        issues.push('available microsoft365.account must have a non-empty id');
      }
    } else if (value.microsoft365.account !== null) {
      issues.push('unrequested or unavailable microsoft365.account must be null');
    }
    validateEvidenceRecords(value.microsoft365.messages, 'microsoft365.messages', 100, (record) => record?.id, issues);
    validateEvidenceRecords(value.microsoft365.events, 'microsoft365.events', 100, (record) => record?.id, issues);
    if (!microsoftAvailable && (value.microsoft365.messages?.length > 0 || value.microsoft365.events?.length > 0)) {
      issues.push('unrequested or unavailable Microsoft 365 evidence must be empty');
    }
  }
  if (isObject(value.slack) && Array.isArray(value.slack.messages)) {
    validateEvidenceRecords(value.slack.conversations, 'slack.conversations', 100, (record) => record?.id, issues);
    validateEvidenceRecords(value.slack.users, 'slack.users', 100, (record) => record?.id, issues);
    validateEvidenceRecords(
      value.slack.messages,
      'slack.messages',
      2_000,
      (record) => isNonEmptyString(record?.channel) && isNonEmptyString(record?.ts)
        ? `${record.channel}|${record.ts}`
        : undefined,
      issues,
    );
    if (!slackAvailable && ['conversations', 'users', 'messages'].some((field) => value.slack[field]?.length > 0)) {
      issues.push('unrequested or unavailable Slack evidence must be empty');
    }
  }
  if (Array.isArray(value.coverage) && value.coverage.length === COVERAGE.length) {
    const derived = recomputeCoverage(value);
    if (JSON.stringify(value.coverage.map((row) => row?.value)) !== JSON.stringify(derived)) {
      issues.push('evidence coverage must match unique evidence records');
    }
  }
  return issues;
}

function validateEvidenceRecords(records, path, maximum, key, issues) {
  if (!Array.isArray(records)) return;
  if (records.length > maximum) issues.push(`${path} exceeds the ${maximum} record limit`);
  const seen = new Set();
  records.forEach((record, index) => {
    if (!isObject(record)) {
      issues.push(`${path}[${index}] must be an object`);
      return;
    }
    const identity = key(record);
    if (!isNonEmptyString(identity)) issues.push(`${path}[${index}] must have a non-empty identity`);
    else if (seen.has(identity)) issues.push(`${path}[${index}] identity must be unique`);
    else seen.add(identity);
  });
}

function recomputeCoverage(evidence) {
  const messages = Array.isArray(evidence.microsoft365?.messages) ? evidence.microsoft365.messages : [];
  const events = Array.isArray(evidence.microsoft365?.events) ? evidence.microsoft365.events : [];
  const slack = Array.isArray(evidence.slack?.messages) ? evidence.slack.messages : [];
  return [
    String(new Set(messages.map((item) => item?.id).filter(isNonEmptyString)).size),
    String(new Set(slack.filter((item) => isNonEmptyString(item?.channel) && isNonEmptyString(item?.ts)).map((item) => `${item.channel}|${item.ts}`)).size),
    String(new Set(events.map((item) => item?.id).filter(isNonEmptyString)).size),
    '0',
  ];
}

function parseJson(path, description, reader = readFileSync) {
  try {
    const raw = reader(path, 'utf8');
    return { raw, value: JSON.parse(raw) };
  } catch {
    throw new Error(`${description} is not readable JSON`);
  }
}

export function publishBrief(io = { readFileSync, renameSync, unlinkSync, writeFileSync }) {
  const candidate = parseJson(CANDIDATE, 'brief candidate', io.readFileSync);
  const evidence = parseJson(EVIDENCE, 'brief evidence', io.readFileSync);
  const candidateIssues = validateBriefCandidate(candidate.value);
  if (candidateIssues.length > 0) throw new Error(`brief candidate failed contract: ${candidateIssues.join('; ')}`);
  const evidenceIssues = validateBriefEvidence(evidence.value);
  if (evidenceIssues.length > 0) throw new Error(`brief evidence failed contract: ${evidenceIssues.join('; ')}`);
  if (candidate.value.evidenceId !== evidence.value.evidenceId) {
    throw new Error('brief candidate does not name the current evidence manifest');
  }
  const now = typeof io.now === 'function' ? io.now() : Date.now();
  const collectedAt = Date.parse(evidence.value.collectedAt);
  const generatedAt = Date.parse(candidate.value.generatedAt);
  if (
    collectedAt > now + MAX_FUTURE_SKEW_MS ||
    now - collectedAt > MAX_EVIDENCE_AGE_MS ||
    generatedAt < collectedAt ||
    generatedAt > now + MAX_FUTURE_SKEW_MS
  ) {
    throw new Error('brief evidence is outside the publication freshness window');
  }
  cleanupStaleTemps(now);
  const sourceIssues = validateCandidateSources(candidate.value, evidence.value);
  if (sourceIssues.length > 0) throw new Error(`brief candidate source binding failed: ${sourceIssues.join('; ')}`);
  const derived = recomputeCoverage(evidence.value);
  const declaredEvidence = evidence.value.coverage.map((row) => row.value);
  const declaredCandidate = candidate.value.coverage.map((row) => row.value);
  if (JSON.stringify(derived) !== JSON.stringify(declaredEvidence) || JSON.stringify(derived) !== JSON.stringify(declaredCandidate)) {
    throw new Error('brief coverage disagrees with current evidence');
  }

  let previous;
  try {
    previous = io.readFileSync(DESTINATION, 'utf8');
  } catch (error) {
    if (error?.code !== 'ENOENT') throw error;
  }
  const temporary = join(WORKSPACE, `.brief.json.${process.pid}.${randomUUID()}.tmp`);
  try {
    io.writeFileSync(temporary, candidate.raw, { encoding: 'utf8', flag: 'wx', mode: 0o600 });
    const staged = parseJson(temporary, 'staged brief candidate', io.readFileSync).value;
    const stagedIssues = validateBriefCandidate(staged);
    if (stagedIssues.length > 0 || staged.evidenceId !== evidence.value.evidenceId) {
      throw new Error('staged brief failed pre-rename validation');
    }
    io.renameSync(temporary, DESTINATION);
  } finally {
    try {
      io.unlinkSync(temporary);
    } catch (error) {
      if (error?.code !== 'ENOENT') throw error;
    }
  }
  try {
    const readBackRaw = io.readFileSync(DESTINATION, 'utf8');
    if (readBackRaw !== candidate.raw) throw new Error('published bytes differ from the candidate');
    const readBack = JSON.parse(readBackRaw);
    const readBackIssues = validateBriefCandidate(readBack);
    if (readBackIssues.length > 0 || readBack.evidenceId !== evidence.value.evidenceId) {
      throw new Error('published content does not match the governed contract');
    }
  } catch (readBackError) {
    try {
      restorePreviousBrief(io, previous);
    } catch (rollbackError) {
      throw new AggregateError(
        [readBackError, rollbackError],
        'published brief failed read-back validation; rollback restoration failed',
      );
    }
    throw new Error('published brief failed read-back validation; previous brief restored', {
      cause: readBackError,
    });
  }
}

function restorePreviousBrief(io, previous) {
  if (previous === undefined) {
    try {
      io.unlinkSync(DESTINATION);
    } catch (error) {
      if (error?.code !== 'ENOENT') throw error;
    }
    return;
  }
  const rollback = join(WORKSPACE, `.brief.rollback.${process.pid}.${randomUUID()}.tmp`);
  try {
    io.writeFileSync(rollback, previous, { encoding: 'utf8', flag: 'wx', mode: 0o600 });
    io.renameSync(rollback, DESTINATION);
  } finally {
    try {
      io.unlinkSync(rollback);
    } catch (error) {
      if (error?.code !== 'ENOENT') throw error;
    }
  }
}

function cleanupStaleTemps(now) {
  for (const name of readdirSync(WORKSPACE)) {
    if (!OWNED_TEMP_NAME.test(name)) continue;
    const path = join(WORKSPACE, name);
    let metadata;
    try {
      metadata = lstatSync(path);
    } catch (error) {
      if (error?.code === 'ENOENT') continue;
      throw error;
    }
    if (metadata.mtimeMs > now - STALE_TEMP_AGE_MS) continue;
    try {
      unlinkSync(path);
    } catch (error) {
      if (error?.code !== 'ENOENT') throw error;
    }
  }
}

function validateCandidateSources(candidate, evidence) {
  const available = new Set(
    evidence.requestedSources.filter((source) => !evidence.unavailable.includes(source)),
  );
  const issues = [];
  const requiredEvidenceSource = { email: 'microsoft365', calendar: 'microsoft365', slack: 'slack' };
  for (const section of ['topOfMind', 'fyi', 'lookingAhead']) {
    for (const item of candidate[section]) {
      const required = requiredEvidenceSource[item.source];
      if (!required || !available.has(required)) {
        issues.push(`${section} item ${item.id} has no requested available evidence source`);
      }
    }
  }
  return issues;
}

async function main() {
  if (process.argv.length !== 2) {
    process.stderr.write('usage: publish-brief.mjs\n');
    process.exitCode = 2;
    return;
  }
  try {
    publishBrief();
    process.stdout.write('published governed brief.json\n');
  } catch (error) {
    process.stderr.write(`${error instanceof Error ? error.message : 'brief publication failed'}\n`);
    process.exitCode = 1;
  }
}

if (process.argv[1] && realpathSync(process.argv[1]) === realpathSync(fileURLToPath(import.meta.url))) {
  await main();
}
