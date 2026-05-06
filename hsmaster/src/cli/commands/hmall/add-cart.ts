import { HyundaiMall } from '../../../malls/hyundai.js';
import { loadAccounts, getAccount } from '../../../malls/base.js';
import { getUsage, incrementUsage } from '../../../db/state.js';
import { limitFor } from '../../../core/limits.js';
import { todayKST } from '../../../core/kst.js';

export interface AddCartOptions {
  url: string;
  qty: number;
  accounts: string; // "1-19" / "1,3,5" / "all"
  clearCart: boolean;
  dryRun: boolean;
  headless: boolean;
  persistProfile?: boolean;
  cdpAttach?: boolean;
  cdpPort?: number;
}

const ACCOUNT_DELAY_SEC = 5; // 가이드 G: 계정 간 대기 (차단 잦으면 30-60초 상향)

export function parseAccountSpec(spec: string, total: number): number[] {
  if (spec === 'all') return Array.from({ length: total }, (_, i) => i + 1);
  const out: number[] = [];
  for (const raw of spec.split(',')) {
    const part = raw.trim();
    if (!part) continue;
    const range = part.match(/^(\d+)-(\d+)$/);
    if (range) {
      const a = parseInt(range[1] ?? '0', 10);
      const b = parseInt(range[2] ?? '0', 10);
      for (let i = a; i <= b; i++) out.push(i);
    } else {
      const n = parseInt(part, 10);
      if (Number.isFinite(n)) out.push(n);
    }
  }
  return Array.from(new Set(out)).filter((n) => n >= 1 && n <= total).sort((a, b) => a - b);
}

function maskId(id: string): string {
  if (id.includes('@')) {
    const [u, d] = id.split('@');
    return `${(u ?? '').slice(0, 2)}***@${d ?? ''}`;
  }
  return id.length <= 3 ? '***' : `${id.slice(0, 2)}***${id.slice(-1)}`;
}

interface AccountResult {
  idx: number;
  id: string;
  status: 'OK' | 'FAIL' | 'LIMIT';
  reason?: string;
}

export async function runAddCart(opts: AddCartOptions): Promise<void> {
  const allAccounts = loadAccounts('hyundai');
  const indices = parseAccountSpec(opts.accounts, allAccounts.length);
  if (indices.length === 0) throw new Error(`Invalid --accounts: '${opts.accounts}' (총 ${allAccounts.length}계정)`);

  const limit = limitFor('hmall_reward', 'hyundai');

  console.log(`▶ Hmall 멀티 계정 cart 담기 (${todayKST()} KST)`);
  console.log(`  URL: ${opts.url}`);
  console.log(`  qty: ${opts.qty} | 대상: ${indices.length}개 (${indices.join(',')}) | clear-cart: ${opts.clearCart} | dry-run: ${opts.dryRun}`);
  console.log('');

  const results: AccountResult[] = [];

  for (let i = 0; i < indices.length; i++) {
    const idx = indices[i]!;
    const account = getAccount(idx, 'hyundai');
    const used = getUsage(idx, 'hmall_reward', 'hyundai');
    if (used >= limit) {
      console.log(`[${i + 1}/${indices.length}] 계정 ${idx} (${maskId(account.id)}) — 한도 ${used}/${limit} 도달, skip`);
      results.push({ idx, id: account.id, status: 'LIMIT', reason: `${used}/${limit}` });
      continue;
    }
    console.log(`[${i + 1}/${indices.length}] 계정 ${idx} (${maskId(account.id)}) 시작 (사용 ${used}/${limit})`);
    const mall = new HyundaiMall({
      headless: opts.headless,
      dryRun: opts.dryRun,
      persistProfile: opts.persistProfile,
      cdpAttach: opts.cdpAttach,
      cdpPort: opts.cdpPort,
    });
    try {
      await mall.loginIfNeeded(account);
      if (opts.clearCart) await mall.clearCart();
      await mall.addToCartByUrl(opts.url, opts.qty);
      console.log(`    ✓ 담기 성공`);
      if (!opts.dryRun) {
        const newCount = incrementUsage(idx, 'hmall_reward', 'hyundai');
        console.log(`    카운터 ${newCount}/${limit}`);
      }
      results.push({ idx, id: account.id, status: 'OK' });
    } catch (e) {
      console.log(`    ✗ 실패: ${(e as Error).message.slice(0, 200)}`);
      results.push({ idx, id: account.id, status: 'FAIL', reason: (e as Error).message.slice(0, 120) });
    } finally {
      await mall.close();
    }
    // 가이드 G: 계정 간 5초 대기 (차단 잦으면 상향)
    if (i < indices.length - 1) await new Promise((r) => setTimeout(r, ACCOUNT_DELAY_SEC * 1000));
  }

  const ok = results.filter((r) => r.status === 'OK').length;
  const fail = results.filter((r) => r.status === 'FAIL').length;
  const limited = results.filter((r) => r.status === 'LIMIT').length;
  console.log('');
  console.log(`▶ 요약: OK ${ok} / FAIL ${fail} / LIMIT ${limited} (총 ${results.length})`);
  for (const r of results) {
    if (r.status !== 'OK') console.log(`  · ${r.idx} (${maskId(r.id)}): ${r.status} — ${r.reason ?? ''}`);
  }
}
