import { chromium as defaultChromium, type BrowserContext, type BrowserType, type Page } from 'playwright';
import * as readline from 'node:readline/promises';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import type { Account, CartItem, Mall } from './base.js';
import type { ProductCode } from '../core/sulwhasoo/products.js';
import { PRODUCTS, PRODUCT_CODES } from '../core/sulwhasoo/products.js';
import { getId, setId } from '../core/sulwhasoo/ids.js';
import { getLoginState, recordLoginSuccess, recordLoginFailure } from '../db/state.js';

type Level = 1 | 2 | 3;

function rand(min: number, max: number): number {
  return Math.floor(min + Math.random() * (max - min + 1));
}
function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}
function maskId(id: string): string {
  if (id.includes('@')) {
    const [u, d] = id.split('@');
    return `${(u ?? '').slice(0, 2)}***@${d ?? ''}`;
  }
  return id.length <= 3 ? '***' : `${id.slice(0, 2)}***${id.slice(-1)}`;
}

let _stealthChromium: BrowserType | null = null;
async function loadStealthChromium(): Promise<BrowserType> {
  if (_stealthChromium) return _stealthChromium;
  const pwExtra = await import('playwright-extra');
  const stealth = (await import('puppeteer-extra-plugin-stealth')).default;
  const c = pwExtra.chromium;
  c.use(stealth());
  _stealthChromium = c as unknown as BrowserType;
  return _stealthChromium;
}

const __dirname = dirname(fileURLToPath(import.meta.url));
const EXTENSION_PATH = resolve(__dirname, '../../../lotte-dialog-blocker');
const CART_URL = 'https://www.lotteimall.com/cart/searchCartList.lotte?tlog=00100_16';

export interface LotteOptions {
  headless?: boolean;
  dryRun?: boolean;
}

export class LotteMall implements Mall {
  private context: BrowserContext | null = null;
  private page: Page | null = null;
  private opts: LotteOptions;
  private rewardEvents: { name: string; topTier: number | null }[] = [];

  constructor(opts: LotteOptions = {}) {
    this.opts = { headless: false, dryRun: false, ...opts };
  }

  printRewardSummary(): void {
    if (this.rewardEvents.length === 0) {
      console.log(`  [적립 이벤트] 없음`);
      return;
    }
    console.log(`  [적립 이벤트] 있음 (${this.rewardEvents.length}건)`);
    for (const ev of this.rewardEvents) {
      const amt = ev.topTier ? ` → 최상위 적립 ${ev.topTier.toLocaleString()}원` : ' → 적립금 추출 실패';
      console.log(`    · ${ev.name}${amt}`);
    }
  }

  private async ensurePage(): Promise<Page> {
    if (this.page) return this.page;
    return this.openContextAtLevel(1);
  }

  private async openContextAtLevel(level: Level): Promise<Page> {
    if (this.context) {
      await this.context.close().catch(() => {});
      this.context = null;
      this.page = null;
    }

    const userDataDir = mkdtempSync(join(tmpdir(), 'lotte-pw-'));
    const args = [
      `--disable-extensions-except=${EXTENSION_PATH}`,
      `--load-extension=${EXTENSION_PATH}`,
    ];

    const chromium: BrowserType = level === 3 ? await loadStealthChromium() : defaultChromium;
    this.context = await chromium.launchPersistentContext(userDataDir, {
      headless: this.opts.headless,
      viewport: { width: 1280, height: 900 },
      locale: 'ko-KR',
      args,
    });
    // 컨텍스트 레벨 dialog 핸들러: 항상 accept (lotte는 confirm 자주 발동)
    this.context.on('page', (p) => {
      p.on('dialog', (d) => d.accept().catch(() => {}));
    });
    const pages = this.context.pages();
    this.page = pages[0] ?? (await this.context.newPage());
    this.page.on('dialog', (d) => d.accept().catch(() => {}));
    return this.page;
  }

  private async dismissOverlays(target: Page): Promise<void> {
    const dismisses = [
      target.getByRole('link', { name: '30일간 보이지 않기' }).first(),
      target.getByRole('link', { name: '일간 보이지 않기' }).first(),
      target.getByRole('button', { name: '닫기', exact: true }).first(),
    ];
    for (const loc of dismisses) {
      if (await loc.isVisible().catch(() => false)) {
        await loc.click().catch(() => {});
        await sleep(250);
      }
    }
  }

  async loginIfNeeded(account: Account): Promise<void> {
    const state = getLoginState(account.index, 'lotte');
    if (state.consecutiveFailures >= 3) {
      throw new Error(
        `계정 ${account.index} (${maskId(account.id)}) 봇 차단 추정 (연속 실패 ${state.consecutiveFailures}회, 마지막 ${state.lastFailureAt}). IP 변경 또는 30분 후 재시도 권장.`
      );
    }
    const startLevel: Level = state.preferStealth ? 3 : 1;
    const levels: Level[] = [];
    for (let l = startLevel; l <= 3; l++) levels.push(l as Level);

    let lastReason = '';
    for (const level of levels) {
      console.log(`  → Level ${level} 로그인 시도${state.preferStealth && level === 3 ? ' (prefer_stealth)' : ''}`);
      const page = await this.openContextAtLevel(level);
      const result = await this.attemptLogin(page, account, level);
      if (result.success) {
        recordLoginSuccess(account.index, 'lotte', level);
        if (level > 1) console.log(`  ✓ 로그인 성공 (Level ${level})`);
        return;
      }
      lastReason = result.reason ?? 'unknown';
      console.log(`  ✗ Level ${level} 실패: ${lastReason}`);
    }
    const total = recordLoginFailure(account.index, 'lotte');
    throw new Error(
      `계정 ${account.index} (${maskId(account.id)}) 봇 차단 추정 — Level ${levels.join('→')} 전부 실패. 누적 ${total}회. IP 변경 또는 30분 후 재시도 권장. 마지막: ${lastReason}`
    );
  }

  private async attemptLogin(page: Page, account: Account, level: Level): Promise<{ success: boolean; reason?: string }> {
    try {
      await page.goto('https://www.lotteimall.com/', { waitUntil: 'domcontentloaded', timeout: 15000 });
      await sleep(rand(1000, 1500));

      // 이미 로그인 상태 검사
      if (await page.locator('text=로그아웃').first().isVisible().catch(() => false)) {
        return { success: true };
      }

      // 헤더 "로그인" 링크 클릭 → popup1 (로그인 폼)
      const loginLink = page.getByRole('link', { name: '로그인 로그인' }).first();
      await loginLink.waitFor({ state: 'visible', timeout: 5000 });
      const popup1Promise = page.waitForEvent('popup', { timeout: 8000 });
      await loginLink.click();
      let popup1: Page;
      try {
        popup1 = await popup1Promise;
      } catch {
        return { success: false, reason: '로그인 popup1 미열림' };
      }
      popup1.on('dialog', (d) => d.accept().catch(() => {}));

      // popup1: ID/PW 휴먼 타이핑
      const idInput = popup1.getByRole('textbox', { name: '아이디 또는 이메일' }).first();
      try {
        await idInput.waitFor({ state: 'visible', timeout: 5000 });
      } catch {
        await popup1.close().catch(() => {});
        return { success: false, reason: 'popup1 ID input 미발견' };
      }
      const pwInput = popup1.getByRole('textbox', { name: /비밀번호/ }).first();
      const charDelay = (): number => rand(50, 150);
      const gapMin = level >= 2 ? 700 : 300;
      const gapMax = level >= 2 ? 1500 : 700;
      await idInput.click();
      await idInput.pressSequentially(account.id, { delay: charDelay() });
      await sleep(rand(gapMin, gapMax));
      await pwInput.click();
      await pwInput.pressSequentially(account.pw, { delay: charDelay() });
      await sleep(rand(gapMin, gapMax));

      // popup1 submit → popup2 (비번 변경 캠페인 등) 가능성
      const submit = popup1.getByRole('link', { name: '로그인', exact: true }).first();
      const popup2Promise = popup1.waitForEvent('popup', { timeout: 5000 });
      await submit.click();
      let popup2: Page | null = null;
      try {
        popup2 = await popup2Promise;
      } catch {
        // popup2 없을 수도 있음 (정상)
      }

      // popup1 닫기 (이미 닫혔을 수도)
      await popup1.close().catch(() => {});
      await sleep(rand(800, 1200));

      // 원본 page를 메인으로 재이동 — codegen 패턴 (이걸 빼먹으면 stale 상태)
      await page.goto('https://www.lotteimall.com/', { waitUntil: 'domcontentloaded', timeout: 15000 });
      await sleep(rand(1500, 2200));

      // popup2 dismiss
      if (popup2) {
        popup2.on('dialog', (d) => d.accept().catch(() => {}));
        await this.dismissOverlays(popup2);
        await popup2.close().catch(() => {});
      }
      await this.dismissOverlays(page);
      // React 렌더 + 세션 정착 대기 — 짧으면 로그인 상태 미반영
      await sleep(5000);
      await this.dismissOverlays(page);

      // 로그인 상태 확인: 다수 신호 OR
      // 1) "로그아웃" 텍스트 visible
      // 2) "로그인 로그인" link 부재
      // 3) "마이페이지" link visible
      const logoutVisible = await page.locator('text=로그아웃').first().isVisible().catch(() => false);
      if (logoutVisible) return { success: true };
      const myPageVisible = await page.getByRole('link', { name: '마이페이지' }).first().isVisible().catch(() => false);
      if (myPageVisible) return { success: true };
      const loginLinkVisible = await page
        .getByRole('link', { name: '로그인 로그인' })
        .first()
        .isVisible()
        .catch(() => false);
      if (!loginLinkVisible) return { success: true };

      // 여전히 로그인 link 보임 → 실제 실패. 원인 진단
      if (
        await page
          .locator('iframe[src*="captcha"], img[src*="captcha"], [class*="captcha"], [id*="captcha"]')
          .first()
          .isVisible()
          .catch(() => false)
      ) {
        return { success: false, reason: '캡차 노출' };
      }
      const body = await page.locator('body').innerText().catch(() => '');
      if (/차단|보안.*문자|이용.*제한|자동.*입력/.test(body)) {
        return { success: false, reason: '차단/보안문자 키워드 감지' };
      }
      if (/(아이디|비밀번호).*확인|일치하지\s*않/.test(body)) {
        return { success: false, reason: '로그인 거부 메시지' };
      }
      return { success: false, reason: '로그인 link 여전히 표시됨' };
    } catch (e) {
      return { success: false, reason: (e as Error).message };
    }
  }

  async clearCart(): Promise<void> {
    // 최대 3회 retry (확실히 빌 때까지)
    for (let attempt = 1; attempt <= 3; attempt++) {
      const page = await this.ensurePage();
      await page.goto(CART_URL, { waitUntil: 'domcontentloaded', timeout: 15000 });
      await sleep(rand(1500, 2200));
      await this.dismissOverlays(page);

      // 헤더 row 제외 상품 row 개수
      const productRowCount = await page.evaluate(() => {
        const rows = Array.from(document.querySelectorAll('.c_item'));
        let n = 0;
        for (const r of rows) {
          if (!r.querySelector('input[id*="AllChk" i], input[name*="AllChk" i]')) n++;
        }
        return n;
      });
      if (productRowCount === 0) return;
      if (attempt > 1) console.log(`        clearCart retry ${attempt}: ${productRowCount}건 잔여 → 재시도`);

      // 1) 전체선택 ("일반 (N/M)" 라벨 클릭)
      const selectAll = page.getByText(/^일반\s*\(\d+\/\d+\)/).first();
      if (await selectAll.isVisible().catch(() => false)) {
        await selectAll.click().catch(() => {});
        await sleep(rand(400, 700));
      }

      // 2) 선택삭제 클릭 (이후 native dialog 또는 in-page modal 발생 가능)
      const deleteBtn = page.getByRole('link', { name: '선택삭제' }).first();
      if (!(await deleteBtn.isVisible().catch(() => false))) return;
      await deleteBtn.click();
      await sleep(rand(800, 1200));

      // 3) in-page modal "예"/"확인" 버튼 처리 (native dialog는 page-level accept 핸들러가 처리)
      for (const loc of [
        page.getByRole('link', { name: '예' }).first(),
        page.getByRole('button', { name: '예' }).first(),
        page.getByRole('link', { name: '확인' }).first(),
        page.getByRole('button', { name: '확인' }).first(),
      ]) {
        if (await loc.isVisible().catch(() => false)) {
          await loc.click().catch(() => {});
          await sleep(rand(600, 1000));
          break;
        }
      }
      await sleep(rand(800, 1300));
      await this.dismissOverlays(page);
    }
    console.log(`        ⚠️ clearCart: 3회 시도 후에도 카트 비우지 못함`);
  }

  async addToCart(code: ProductCode, qty: number): Promise<void> {
    const page = await this.ensurePage();
    const goodsNo = getId(code, 'lotte');

    // 직접 URL 진입 (검색·매칭 우회 — DOM 변동에 영향 없음)
    const url = `https://www.lotteimall.com/goods/viewGoodsDetail.lotte?goods_no=${goodsNo}`;
    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 15000 });
    await sleep(rand(1500, 2500));
    await this.dismissOverlays(page);

    // 상품 페이지 유효성 검증 (만료/리다이렉트 감지)
    await this.verifyProductPage(code, page, goodsNo);

    // 쿠폰받기 → 전체 다운로드 → 닫기
    await this.collectCoupons(page);

    // 적립 이벤트 처리
    await this.processRewardEvents(page, code);

    // 옵션 선택 (타입 선택 → 세트)
    await this.selectOption(page, code);

    // 수량 증가 — qty input 값 읽고 target까지 +클릭 (초기값 0/1 무관 robust)
    // lotte는 qty 표시가 input value 또는 .spinner 내부 텍스트일 수 있음
    const qtyInput = page
      .locator('input[name="ordQty"], input[name="orderCnt"], input[name="goodsCnt"], input[type="text"][title*="수량"], .spinner input')
      .first();
    const plusBtn = page.getByRole('button', { name: '+' }).first();
    const readQty = async (): Promise<number | null> => {
      try {
        const v = await qtyInput.inputValue({ timeout: 1000 });
        return parseInt(v || '0', 10) || 0;
      } catch {
        // input 못 찾으면 .spinner 내 숫자 텍스트 시도
        const t = await page.locator('.spinner').first().textContent({ timeout: 1000 }).catch(() => null);
        if (!t) return null;
        const m = t.match(/(\d+)/);
        return m ? parseInt(m[1] || '0', 10) : null;
      }
    };
    let cur = await readQty();
    if (cur === null) {
      // qty input 미발견: (qty-1)회 클릭 (initial=1 가정)
      if (qty > 1) {
        for (let i = 1; i < qty; i++) {
          await plusBtn.click().catch(() => {});
          await sleep(rand(120, 220));
        }
      }
    } else {
      let safety = qty + 8;
      while (cur < qty && safety-- > 0) {
        await plusBtn.click().catch(() => {});
        await sleep(rand(120, 220));
        const next = await readQty();
        if (next === null) break;
        cur = next;
      }
      if (cur !== qty) {
        console.log(`        ⚠️ qty 미달: 기대 ${qty}, 실제 ${cur} (code=${code})`);
      }
    }

    // 장바구니
    const saveBtn = page.locator('#saveCart-btn').first();
    try {
      await saveBtn.waitFor({ state: 'visible', timeout: 5000 });
    } catch {
      throw new Error(`#saveCart-btn 미발견 (code=${code}, URL=${page.url()})`);
    }
    await saveBtn.click();
    await sleep(rand(1500, 2500));
    await this.dismissOverlays(page);
  }

  private async verifyProductPage(code: ProductCode, page: Page, goodsNo: string): Promise<void> {
    const url = page.url();
    if (!url.includes('viewGoodsDetail') || !url.includes(goodsNo)) {
      await this.handleExpiredId(code, goodsNo, `URL 리다이렉트: ${url}`);
      return;
    }
    const bodyText = await page.locator('body').innerText().catch(() => '');
    if (/상품을 찾을 수 없|존재하지 않는 상품|판매가 종료|판매중지/.test(bodyText)) {
      await this.handleExpiredId(code, goodsNo, '상품 없음/종료 메시지 감지');
      return;
    }
    const hasOption = await page.getByRole('link', { name: '타입 선택' }).first().isVisible().catch(() => false);
    const hasSaveBtn = await page.locator('#saveCart-btn').first().isVisible().catch(() => false);
    if (!hasOption && !hasSaveBtn) {
      await this.handleExpiredId(code, goodsNo, '옵션·장바구니 버튼 미노출 (페이지 구조 변경 의심)');
      return;
    }
  }

  // 검색 기반 매칭은 deprecated — 직접 URL 진입으로 대체. 향후 fallback용으로 유지.
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  private async clickProductInSearch(page: Page, code: ProductCode): Promise<boolean> {
    const expectedName = PRODUCTS[code].name.split('(')[0]?.trim() ?? '';
    const expectedNorm = expectedName.replace(/\s+/g, '');

    // 매칭 룰: anchor 자기 텍스트(상품명)로 60% 매칭 → 그 anchor의 ancestor에 "백화점" 이미지 배지 필수
    //          → 광세일 배지 보너스. 이 순서로 해야 다중 상품 컨테이너 false positive 회피.
    // page.evaluate 내부에서는 함수 선언 금지 (tsx __name 헬퍼 주입 회피)
    const evalResult = await page.evaluate(
      ({ expected }: { expected: string }) => {
        const anchors = Array.from(
          document.querySelectorAll('a[href*="viewGoodsDetail"], a[href*="goods_no="]')
        ) as HTMLAnchorElement[];
        let bestHref: string | null = null;
        let bestScore = -1;
        let bestName = '';
        let nameMatchCount = 0;
        let badgeAncestorCount = 0;
        for (const a of anchors) {
          if (!a.href) continue;
          // (1) anchor 자기 텍스트(상품명) — 자기 textContent + 자식 img alt + aria-label
          const imgAlts = Array.from(a.querySelectorAll('img'))
            .map((img) => img.getAttribute('alt') || '')
            .join(' ');
          const rawName =
            (a.textContent || '') +
            ' ' +
            imgAlts +
            ' ' +
            (a.getAttribute('aria-label') || '') +
            ' ' +
            (a.getAttribute('title') || '');
          const anchorText = rawName.replace(/\s+/g, '');
          // (2) 순서보존 매칭
          let matches = 0;
          let lastIdx = 0;
          for (const ch of expected) {
            const idx = anchorText.indexOf(ch, lastIdx);
            if (idx >= 0) {
              matches++;
              lastIdx = idx + 1;
            }
          }
          const sim = expected.length > 0 ? matches / expected.length : 0;
          if (sim < 0.6) continue;
          nameMatchCount++;
          // (3) ancestor 트리에서 "백화점" 이미지 배지 확인 (max 8 depth)
          let n: HTMLElement | null = a;
          let card: HTMLElement | null = null;
          for (let d = 0; d < 8 && n; d++) {
            if (n.querySelector('img[alt="백화점"], img[src*="logo_lotted" i]')) {
              card = n;
              break;
            }
            n = n.parentElement;
          }
          if (!card) continue;
          badgeAncestorCount++;
          // (4) 광세일 배지 보너스
          const cardText = (card.textContent || '').replace(/\s+/g, '');
          const hasSale =
            cardText.includes('광세일') ||
            !!card.querySelector(
              'img[alt*="세일"], img[alt*="광세일"], img[src*="sale" i], [class*="badge"][class*="sale" i]'
            );
          const score = sim + (hasSale ? 0.3 : 0);
          if (score > bestScore) {
            bestScore = score;
            bestHref = a.href;
            bestName = (a.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 80);
          }
        }
        return {
          href: bestHref,
          anchorCount: anchors.length,
          nameMatchCount,
          badgeAncestorCount,
          bestScore,
          bestName,
        };
      },
      { expected: expectedNorm }
    );

    const targetHref = evalResult.href;

    if (targetHref) {
      await page.goto(targetHref, { waitUntil: 'domcontentloaded', timeout: 15000 });
      return true;
    }

    // 롯데백화점 라벨 카드 0개 = 비정상 상황. fallback 금지, 명시적 실패.
    return false;
  }

  private async collectCoupons(page: Page): Promise<void> {
    const couponBtn = page.getByRole('button', { name: '쿠폰받기' }).first();
    if (!(await couponBtn.isVisible().catch(() => false))) return;
    await couponBtn.click();
    await sleep(rand(800, 1300));
    const allBtn = page.getByRole('button', { name: '쿠폰 전체 다운로드' }).first();
    if (await allBtn.isVisible().catch(() => false)) {
      await allBtn.click();
      await sleep(rand(800, 1500));
    }
    const close = page.getByRole('button', { name: '닫기', exact: true }).first();
    if (await close.isVisible().catch(() => false)) {
      await close.click().catch(() => {});
      await sleep(400);
    }
  }

  private async selectOption(page: Page, code: ProductCode): Promise<void> {
    const typeBtn = page.getByRole('link', { name: '타입 선택' }).first();
    const typeBtnVisible = await typeBtn.isVisible().catch(() => false);
    if (typeBtnVisible) {
      await typeBtn.click();
      await sleep(rand(400, 700));
    }
    const setOption = page.getByRole('link', { name: '세트', exact: true }).first();
    const setVisible = await setOption.isVisible().catch(() => false);
    if (setVisible) {
      await setOption.click();
      await sleep(rand(400, 700));
    }
    if (!typeBtnVisible || !setVisible) {
      console.log(`        ⚠️ 옵션 selector 미매칭: 타입선택=${typeBtnVisible}, 세트=${setVisible} (code=${code})`);
    }
  }

  private async processRewardEvents(page: Page, code: ProductCode): Promise<void> {
    // 1) "구매사은/혜택" 섹션 찾기 → 항목 locator 수집
    const headerLoc = page.locator('h3.title').filter({ hasText: /구매사은|혜택/ }).first();
    if (!(await headerLoc.isVisible().catch(() => false))) return;
    const list = headerLoc.locator('xpath=following-sibling::*[1]');
    const allItems = await list.locator('a, li').all();
    if (allItems.length === 0) return;

    // 2) 텍스트 추출 + dedup + 매칭 (positive: '최대 N% 적립')
    const seen = new Set<string>();
    const matchedLocs: { loc: (typeof allItems)[number]; name: string }[] = [];
    let skippedCount = 0;
    for (const item of allItems) {
      const text = ((await item.textContent().catch(() => '')) ?? '').trim();
      if (!text) continue;
      const compact = text.replace(/\s+/g, ' ');
      if (seen.has(compact)) continue;
      seen.add(compact);
      if (/최대\s*\d+\s*%\s*적립/.test(compact)) {
        matchedLocs.push({ loc: item, name: compact.slice(0, 70) });
      } else {
        skippedCount++;
      }
    }

    // 3) 매칭된 항목 각각: 클릭 → 팝업의 최상위 적립금 추출 → 닫기
    for (const m of matchedLocs) {
      let topTier: number | null = null;
      try {
        await m.loc.click({ timeout: 3000 });
        await sleep(rand(800, 1300));
        topTier = await this.extractTopTierAmount(page);
      } catch {
        /* click/extract 실패 — topTier null 유지 */
      } finally {
        await this.closeRewardPopup(page);
      }
      this.rewardEvents.push({ name: m.name, topTier });
    }

    // 4) 상품별 로그
    if (matchedLocs.length) {
      console.log(`        [적립 이벤트] ${matchedLocs.length}건 (${code})`);
      const recent = this.rewardEvents.slice(-matchedLocs.length);
      for (const ev of recent) {
        const amt = ev.topTier ? ` → 최상위 ${ev.topTier.toLocaleString()}원` : ' → 추출 실패';
        console.log(`          · ${ev.name}${amt}`);
      }
    }
    if (skippedCount > 0) {
      console.log(`        (페이백/멤버십 등 ${skippedCount}건 자동 skip)`);
    }
  }

  private async extractTopTierAmount(page: Page): Promise<number | null> {
    return await page.evaluate(() => {
      const text = document.body.innerText || '';
      const re = /적립금\s*([\d,]+)\s*원/g;
      let max = 0;
      let m: RegExpExecArray | null;
      while ((m = re.exec(text)) !== null) {
        const v = parseInt((m[1] || '').replace(/,/g, ''), 10);
        if (v > max) max = v;
      }
      return max > 0 ? max : null;
    });
  }

  private async closeRewardPopup(page: Page): Promise<void> {
    await page.keyboard.press('Escape').catch(() => {});
    await sleep(200);
    for (const loc of [
      page.getByRole('button', { name: '닫기', exact: true }).first(),
      page.getByRole('button', { name: /레이어 닫기/ }).first(),
    ]) {
      if (await loc.isVisible().catch(() => false)) {
        await loc.click().catch(() => {});
        await sleep(200);
        return;
      }
    }
  }

  async applyCoupons(): Promise<void> {
    // 롯데: 쿠폰은 addToCart 시점에 상품 페이지에서 받음 → 카트 단계 no-op
  }

  async listCart(): Promise<CartItem[]> {
    const page = await this.ensurePage();
    await page.goto(CART_URL, { waitUntil: 'domcontentloaded', timeout: 15000 });
    await sleep(rand(1500, 2200));
    await this.dismissOverlays(page);

    // 1. 헤더 row 제외한 상품 row 개수
    const productRowCount = await page.evaluate(() => {
      const rows = Array.from(document.querySelectorAll('.c_item'));
      let count = 0;
      for (const row of rows) {
        if (!row.querySelector('input[id*="AllChk" i], input[name*="AllChk" i]')) {
          count++;
        }
      }
      return count;
    });
    if (productRowCount === 0) return [];

    // 2. "일반 (N/M)" 전체선택 토글 클릭 → 상품금액에 합계 표시되도록
    const selectAll = page.getByText(/^일반\s*\(\d+\/\d+\)/).first();
    if (await selectAll.isVisible().catch(() => false)) {
      await selectAll.click().catch(() => {});
      await sleep(rand(600, 1000));
    }

    // 3. <th scope="row">상품금액</th>의 같은 tr 안 td에서 합계 추출
    const total = await page.evaluate(() => {
      const ths = Array.from(document.querySelectorAll('th[scope="row"]'));
      for (const th of ths) {
        if ((th.textContent || '').trim() === '상품금액') {
          const tr = th.closest('tr');
          const td = tr?.querySelector('td') || (th.nextElementSibling as HTMLElement | null);
          const text = (td?.textContent || '').trim();
          const m = text.match(/([\d,]+)\s*원/);
          return m && m[1] ? parseInt(m[1].replace(/,/g, ''), 10) : null;
        }
      }
      return null;
    });

    if (total === null) {
      return [{ productName: `[lotte 카트 ${productRowCount}건 — 상품금액 읽기 실패]`, qty: productRowCount }];
    }
    return [{ productName: `[lotte 카트 ${productRowCount}건 합계]`, qty: 1, price: total }];
  }

  async close(): Promise<void> {
    if (this.context) {
      await this.context.close().catch(() => {});
    }
    this.context = null;
    this.page = null;
  }

  private async handleExpiredId(code: ProductCode, oldGoodsNo: string, reason: string): Promise<void> {
    const expected = PRODUCTS[code].name;
    console.log('');
    console.log(`⚠️  제품 '${code} (${expected})'의 lotte goods_no=${oldGoodsNo}가 더 이상 유효하지 않습니다.`);
    console.log(`   사유: ${reason}`);
    if (!process.stdin.isTTY) {
      throw new Error(`goods_no 만료 (비대화 모드) — code=${code}, goods_no=${oldGoodsNo}, 사유: ${reason}`);
    }
    console.log(`→ 새 goods_no 또는 lotteimall.com URL 입력 (skip / abort 가능):`);
    const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
    const answer = (await rl.question('새 goods_no: ')).trim();
    rl.close();
    if (answer === 'abort' || answer === '') {
      throw new Error(`Aborted by user — ${code} goods_no expired`);
    }
    if (answer === 'skip') {
      throw new Error(`Skipped ${code} — combo will be incomplete`);
    }
    const m = answer.match(/goods_no=(\d+)/);
    const newId = m?.[1] ?? answer.replace(/[^0-9]/g, '');
    if (!newId) throw new Error(`Invalid goods_no input: ${answer}`);
    setId(code, 'lotte', newId);
    console.log(`✓ config 갱신 완료: ${code} → ${newId}. 재시도하려면 명령어를 다시 실행하세요.`);
    throw new Error(`goods_no 갱신됨 (${code}=${newId}) — 명령어 재실행 필요`);
  }
}
