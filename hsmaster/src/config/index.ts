import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import type { Mall } from '../core/limits.js';

const __dirname = dirname(fileURLToPath(import.meta.url));
const DEFAULT_DIR = resolve(__dirname, '../../../');

const ENV_KEY: Record<Mall, string> = {
  hyundai: 'HSM_HMALL_JSON',
  lotte: 'HSM_LOTTE_JSON',
  galleria: 'HSM_GALLERIA_JSON',
};

const DEFAULT_FILE: Record<Mall, string> = {
  hyundai: 'hmall_config.json',
  lotte: 'lotte.json',
  galleria: 'galleria.json',
};

export function accountsPath(mall: Mall): string {
  const fromEnv = process.env[ENV_KEY[mall]];
  if (fromEnv && fromEnv.trim()) return resolve(fromEnv);
  return resolve(DEFAULT_DIR, DEFAULT_FILE[mall]);
}
