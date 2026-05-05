import { getCombo } from '../../../core/sulwhasoo/combos.js';
import { PRODUCTS } from '../../../core/sulwhasoo/products.js';
import { lastUpdatedAgeDays } from '../../../core/sulwhasoo/ids.js';
import { limitFor, type Mall as MallName } from '../../../core/limits.js';
import { getUsage, incrementUsage } from '../../../db/state.js';
import { loadAccounts, getAccount, type Mall } from '../../../malls/base.js';
import { HyundaiMall } from '../../../malls/hyundai.js';
import { LotteMall } from '../../../malls/lotte.js';
import { GalleriaMall } from '../../../malls/galleria.js';

export interface CartCmdOptions {
  combo: number;
  mall: MallName;
  account?: number;
  dryRun: boolean;
  headless: boolean;
}

function pickAvailableAccount(mall: MallName): number {
  const accounts = loadAccounts(mall);
  for (const a of accounts) {
    if (getUsage(a.index, 'sulwhasoo', mall) < limitFor('sulwhasoo', mall)) {
      return a.index;
    }
  }
  throw new Error(`오늘 모든 ${accounts.length}계정이 ${mall} 한도(${limitFor('sulwhasoo', mall)}/일)에 도달했습니다.`);
}

function makeMall(name: MallName, opts: { headless: boolean; dryRun: boolean }): Mall {
  if (name === 'hyundai') return new HyundaiMall(opts);
  if (name === 'lotte') return new LotteMall(opts);
  if (name === 'galleria') return new GalleriaMall(opts);
  throw new Error(`Mall '${name}'는 미구현.`);
}

export async function runCart(opts: CartCmdOptions): Promise<void> {
  const combo = getCombo(opts.combo);
  const accountIdx = opts.account ?? pickAvailableAccount(opts.mall);
  const account = getAccount(accountIdx, opts.mall);

  const used = getUsage(accountIdx, 'sulwhasoo', opts.mall);
  const limit = limitFor('sulwhasoo', opts.mall);
  if (used >= limit) {
    console.error(`❌ 계정 ${accountIdx} (${account.id})는 오늘 ${opts.mall} 한도 도달: ${used}/${limit}`);
    try {
      const next = pickAvailableAccount(opts.mall);
      console.error(`   → 추천 계정: --account ${next}`);
    } catch (e) {
      console.error(`   → ${(e as Error).message}`);
    }
    process.exit(1);
  }

  const age = lastUpdatedAgeDays();
  if (age >= 30) {
    console.log(`⚠️  config/sulwhasoo-ids.json 마지막 갱신 ${age}일 경과 — 월 갱신 시기일 가능성.`);
  }

  console.log(`▶ 설화수 cart — combo ${combo.id}, mall=${opts.mall}, account=${accountIdx} (${account.id}), 사용=${used}/${limit}`);
  console.log(`  조합 구성: ${combo.items.map((i) => `${i.code}×${i.qty} (${PRODUCTS[i.code].name.split('(')[0]?.trim()})`).join(', ')}`);

  const mall = makeMall(opts.mall, { headless: opts.headless, dryRun: opts.dryRun });
  try {
    console.log(`  [1/5] 로그인...`);
    await mall.loginIfNeeded(account);

    console.log(`  [2/5] 장바구니 비움...`);
    await mall.clearCart();

    console.log(`  [3/5] 조합 ${combo.id} 담기 (${combo.items.length}종)...`);
    for (const item of combo.items) {
      console.log(`        - ${item.code} (${PRODUCTS[item.code].name}) × ${item.qty}`);
      await mall.addToCart(item.code, item.qty);
    }

    console.log(`  [4/5] 쿠폰받기...`);
    await mall.applyCoupons();

    console.log(`  [5/5] 장바구니 확인...`);
    const cart = await mall.listCart();
    console.log(`✓ 장바구니 ${cart.length}건:`);
    for (const c of cart) {
      console.log(`    · ${c.productName} × ${c.qty}${c.price ? ` — ${c.price.toLocaleString()}원` : ''}`);
    }
    // lotte/galleria: 카트 합계 = 조합 전체 소비자가 합계로 자동 검증 (할인 전 retailPrice × qty)
    if (opts.mall === 'lotte' || opts.mall === 'galleria') {
      const expectedTotal = combo.items.reduce(
        (sum, it) => sum + PRODUCTS[it.code].retailPrice * it.qty,
        0
      );
      const actualTotal = cart.reduce((sum, c) => sum + (c.price ?? 0), 0);
      if (actualTotal === expectedTotal) {
        console.log(`  ✓ 카트 합계 ${actualTotal.toLocaleString()}원 = 기대 ${expectedTotal.toLocaleString()}원 (검증 OK)`);
      } else if (actualTotal > 0) {
        console.log(`  ✗ 카트 합계 ${actualTotal.toLocaleString()}원 ≠ 기대 ${expectedTotal.toLocaleString()}원 (차이 ${(actualTotal - expectedTotal).toLocaleString()})`);
      }
    }

    // lotte: 적립 이벤트 최종 요약 (있음/없음 + 매칭된 이벤트별 최상위 적립금)
    if (mall instanceof LotteMall) {
      mall.printRewardSummary();
    }

    if (!opts.dryRun) {
      const newCount = incrementUsage(accountIdx, 'sulwhasoo', opts.mall);
      console.log(`✓ 사용 카운터 +1 → ${newCount}/${limit}`);
    } else {
      console.log(`  (dry-run: 카운터 미증가)`);
    }
  } finally {
    if (!opts.dryRun) {
      console.log(`  브라우저 종료 대기 5초... (Ctrl+C로 즉시 종료)`);
      await new Promise((r) => setTimeout(r, 5000));
    }
    await mall.close();
  }
}
