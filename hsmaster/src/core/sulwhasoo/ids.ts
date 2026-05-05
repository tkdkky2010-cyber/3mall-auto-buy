import { readFileSync, writeFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import type { ProductCode } from './products.js';
import type { Mall } from '../limits.js';
import { todayKST } from '../kst.js';

const __dirname = dirname(fileURLToPath(import.meta.url));
const CONFIG_PATH = resolve(__dirname, '../../../config/sulwhasoo-ids.json');

interface IdsFile {
  lastUpdated: string;
  ids: Record<ProductCode, { name: string; galleria: string; hyundai: string; lotte: string }>;
}

function load(): IdsFile {
  const raw = readFileSync(CONFIG_PATH, 'utf8');
  return JSON.parse(raw) as IdsFile;
}

export function getId(code: ProductCode, mall: Mall): string {
  const f = load();
  const entry = f.ids[code];
  if (!entry) throw new Error(`No ID entry for product ${code}`);
  const v = entry[mall];
  if (!v) throw new Error(`No ${mall} ID for product ${code} — update config/sulwhasoo-ids.json`);
  return v;
}

export function setId(code: ProductCode, mall: Mall, value: string): void {
  const f = load();
  f.ids[code][mall] = value;
  f.lastUpdated = todayKST();
  writeFileSync(CONFIG_PATH, JSON.stringify(f, null, 2) + '\n', 'utf8');
}

export function lastUpdatedAgeDays(): number {
  const f = load();
  const last = new Date(f.lastUpdated + 'T00:00:00+09:00').getTime();
  const now = new Date(todayKST() + 'T00:00:00+09:00').getTime();
  return Math.floor((now - last) / 86400000);
}
