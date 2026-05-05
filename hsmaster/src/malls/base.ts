import { readFileSync } from 'node:fs';
import type { ProductCode } from '../core/sulwhasoo/products.js';
import type { Mall as MallName } from '../core/limits.js';
import { accountsPath } from '../config/index.js';

export interface Account {
  index: number;
  id: string;
  pw: string;
}

export interface CartItem {
  productName: string;
  qty: number;
  price?: number;
}

export interface Mall {
  loginIfNeeded(account: Account): Promise<void>;
  clearCart(): Promise<void>;
  addToCart(code: ProductCode, qty: number): Promise<void>;
  applyCoupons(): Promise<void>;
  listCart(): Promise<CartItem[]>;
  close(): Promise<void>;
}

export function loadAccounts(mall: MallName = 'hyundai'): Account[] {
  const path = accountsPath(mall);
  const raw = readFileSync(path, 'utf8');
  const data = JSON.parse(raw) as { accounts: Array<{ id: string; pw: string }> };
  if (!Array.isArray(data.accounts)) {
    throw new Error(`${mall} 계정 파일에 'accounts' 배열이 없음 (${path})`);
  }
  return data.accounts.map((a, i) => ({ index: i + 1, id: a.id, pw: a.pw }));
}

export function getAccount(index: number, mall: MallName = 'hyundai'): Account {
  const accounts = loadAccounts(mall);
  const a = accounts.find((x) => x.index === index);
  if (!a) throw new Error(`Unknown account index: ${index} (valid: 1~${accounts.length})`);
  return a;
}
