import Database from 'better-sqlite3';
import { mkdirSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import type { Track, Mall } from '../core/limits.js';
import { todayKST } from '../core/kst.js';

const __dirname = dirname(fileURLToPath(import.meta.url));
const DB_PATH = resolve(__dirname, '../../data/state.db');

let _db: Database.Database | null = null;

function db(): Database.Database {
  if (_db) return _db;
  mkdirSync(dirname(DB_PATH), { recursive: true });
  const d = new Database(DB_PATH);
  d.pragma('journal_mode = WAL');
  d.exec(`
    CREATE TABLE IF NOT EXISTS account_usage (
      account_id INTEGER NOT NULL,
      track      TEXT    NOT NULL,
      mall       TEXT    NOT NULL,
      date       TEXT    NOT NULL,
      count      INTEGER NOT NULL DEFAULT 0,
      PRIMARY KEY (account_id, track, mall, date)
    );
    CREATE TABLE IF NOT EXISTS account_login_state (
      account_id           INTEGER NOT NULL,
      mall                 TEXT    NOT NULL,
      prefer_stealth       INTEGER NOT NULL DEFAULT 0,
      consecutive_failures INTEGER NOT NULL DEFAULT 0,
      last_failure_at      TEXT,
      PRIMARY KEY (account_id, mall)
    );
  `);
  _db = d;
  return d;
}

export function getUsage(accountId: number, track: Track, mall: Mall, date: string = todayKST()): number {
  const row = db()
    .prepare<[number, Track, Mall, string]>(
      'SELECT count FROM account_usage WHERE account_id=? AND track=? AND mall=? AND date=?'
    )
    .get(accountId, track, mall, date) as { count: number } | undefined;
  return row?.count ?? 0;
}

export function incrementUsage(accountId: number, track: Track, mall: Mall, date: string = todayKST()): number {
  const cur = getUsage(accountId, track, mall, date);
  const next = cur + 1;
  db()
    .prepare<[number, Track, Mall, string, number]>(
      `INSERT INTO account_usage (account_id, track, mall, date, count) VALUES (?, ?, ?, ?, ?)
       ON CONFLICT(account_id, track, mall, date) DO UPDATE SET count = excluded.count`
    )
    .run(accountId, track, mall, date, next);
  return next;
}

export function getUsageMatrix(date: string = todayKST()): Map<string, number> {
  const rows = db()
    .prepare<[string]>('SELECT account_id, track, mall, count FROM account_usage WHERE date=?')
    .all(date) as Array<{ account_id: number; track: Track; mall: Mall; count: number }>;
  const m = new Map<string, number>();
  for (const r of rows) {
    m.set(`${r.account_id}|${r.track}|${r.mall}`, r.count);
  }
  return m;
}

export interface LoginState {
  preferStealth: boolean;
  consecutiveFailures: number;
  lastFailureAt: string | null;
}

export function getLoginState(accountId: number, mall: Mall): LoginState {
  const row = db()
    .prepare<[number, Mall]>(
      'SELECT prefer_stealth, consecutive_failures, last_failure_at FROM account_login_state WHERE account_id=? AND mall=?'
    )
    .get(accountId, mall) as
    | { prefer_stealth: number; consecutive_failures: number; last_failure_at: string | null }
    | undefined;
  return {
    preferStealth: !!(row?.prefer_stealth ?? 0),
    consecutiveFailures: row?.consecutive_failures ?? 0,
    lastFailureAt: row?.last_failure_at ?? null,
  };
}

export function recordLoginSuccess(accountId: number, mall: Mall, level: 1 | 2 | 3): void {
  const prev = getLoginState(accountId, mall);
  const preferStealth = level === 3 ? 1 : prev.preferStealth ? 1 : 0;
  db()
    .prepare<[number, Mall, number]>(
      `INSERT INTO account_login_state (account_id, mall, prefer_stealth, consecutive_failures, last_failure_at)
       VALUES (?, ?, ?, 0, NULL)
       ON CONFLICT(account_id, mall) DO UPDATE SET
         prefer_stealth       = excluded.prefer_stealth,
         consecutive_failures = 0,
         last_failure_at      = NULL`
    )
    .run(accountId, mall, preferStealth);
}

export function recordLoginFailure(accountId: number, mall: Mall): number {
  const now = new Date().toISOString();
  db()
    .prepare<[number, Mall, string]>(
      `INSERT INTO account_login_state (account_id, mall, prefer_stealth, consecutive_failures, last_failure_at)
       VALUES (?, ?, 0, 1, ?)
       ON CONFLICT(account_id, mall) DO UPDATE SET
         consecutive_failures = consecutive_failures + 1,
         last_failure_at      = excluded.last_failure_at`
    )
    .run(accountId, mall, now);
  return getLoginState(accountId, mall).consecutiveFailures;
}
