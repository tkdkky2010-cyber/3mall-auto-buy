import { chromium as defaultChromium, type Browser, type BrowserContext, type BrowserType, type Page } from 'playwright';
import * as readline from 'node:readline/promises';
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

function maskId(id: string): string {
  if (id.includes('@')) {
    const [u, d] = id.split('@');
    return `${(u ?? '').slice(0, 2)}***@${d ?? ''}`;
  }
  return id.length <= 3 ? '***' : `${id.slice(0, 2)}***${id.slice(-1)}`;
}

export interface HyundaiOptions {
  headless?: boolean;
  dryRun?: boolean;
}

export class HyundaiMall implements Mall {
  private browser: Browser | null = null;
  private context: BrowserContext | null = null;
  private page: Page | null = null;
  private opts: HyundaiOptions;

  constructor(opts: HyundaiOptions = {}) {
    this.opts = { headless: false, dryRun: false, ...opts };
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
    if (this.browser) {
      await this.browser.close().catch(() => {});
      this.browser = null;
    }
    const browserType: BrowserType = level === 3 ? await loadStealthChromium() : defaultChromium;
    this.browser = await browserType.launch({ headless: this.opts.headless });
    this.context = await this.browser.newContext({
      viewport: { width: 1280, height: 900 },
      locale: 'ko-KR',
    });
    this.page = await this.context.newPage();
    return this.page;
  }

  async loginIfNeeded(account: Account): Promise<void> {
    const state = getLoginState(account.index, 'hyundai');
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
        recordLoginSuccess(account.index, 'hyundai', level);
        if (level > 1) console.log(`  ✓ 로그인 성공 (Level ${level})`);
        return;
      }
      lastReason = result.reason ?? 'unknown';
      console.log(`  ✗ Level ${level} 실패: ${lastReason}`);
    }

    const total = recordLoginFailure(account.index, 'hyundai');
    throw new Error(
      `계정 ${account.index} (${maskId(account.id)}) 봇 차단 추정 — Level ${levels.join('→')} 전부 실패. 누적 ${total}회. IP 변경 또는 30분 후 재시도 권장. 마지막 사유: ${lastReason}`
    );
  }

  private async dismissOverlays(page: Page): Promise<void> {
    // 메인 배너 팝업: 닫기 버튼이 두 개 (오늘 그만 보기 / 닫기) — 단순 닫기 클릭
    const banner = page.locator('[role="dialog"][aria-label*="배너"] button:has-text("닫기")').first();
    if (await banner.isVisible().catch(() => false)) {
      await banner.click().catch(() => {});
      await sleep(300);
    }
    // 휴면고객안내 레이어 팝업: 로그인 후 일부 계정에 등장 — "닫기"로 통과
    const dormant = page.locator('[role="dialog"][aria-label*="휴면"] button:has-text("닫기")').first();
    if (await dormant.isVisible().catch(() => false)) {
      await dormant.click().catch(() => {});
      await sleep(300);
    }
  }

  private async attemptLogin(page: Page, account: Account, level: Level): Promise<{ success: boolean; reason?: string }> {
    try {
      await page.goto('https://www.hmall.com/mo/cob/loginForm', { waitUntil: 'domcontentloaded', timeout: 15000 });
      await sleep(rand(700, 1100));
      await this.dismissOverlays(page);

      // 로그인 폼이 렌더될 때까지 명시적 대기 (Level 1 race condition 방지)
      const idInput = page.locator('input#userid').first();
      try {
        await idInput.waitFor({ state: 'visible', timeout: 5000 });
      } catch {
        if (await page.locator('text=로그아웃').first().isVisible().catch(() => false)) {
          return { success: true };
        }
        return { success: false, reason: '로그인 폼 미발견 (URL=' + page.url() + ')' };
      }

      const pwInput = page.locator('input#password').first();
      const gapMin = level >= 2 ? 700 : 300;
      const gapMax = level >= 2 ? 1500 : 700;
      const charDelay = (): number => rand(50, 150);

      await idInput.click();
      await idInput.pressSequentially(account.id, { delay: charDelay() });
      await sleep(rand(gapMin, gapMax));
      await pwInput.click();
      await pwInput.pressSequentially(account.pw, { delay: charDelay() });
      await sleep(rand(gapMin, gapMax));

      const loginBtn = page.locator('button:has-text("로그인")').first();
      await loginBtn.click();
      try {
        await page.waitForLoadState('domcontentloaded', { timeout: 8000 });
      } catch {
        /* settle */
      }
      await sleep(rand(1200, 2200));
      await this.dismissOverlays(page);

      // 1차 성공 신호: URL이 로그인폼에서 떠남 + 로그아웃 링크
      const url = page.url();
      const stillOnLogin = /\/cob\/loginForm/.test(url);
      if (!stillOnLogin) {
        if (await page.locator('text=로그아웃').first().isVisible().catch(() => false)) {
          return { success: true };
        }
        // 페이지는 떠났지만 로그아웃 link 미발견 — callback url로 이동했을 수도. 잠시 더 대기 후 재확인
        await sleep(800);
        if (await page.locator('text=로그아웃').first().isVisible().catch(() => false)) {
          return { success: true };
        }
        return { success: false, reason: 'logout 링크 미발견 (post-login URL=' + url + ')' };
      }

      // 2차: 로그인폼에 머물러 있음 = 봇/자격 거부
      if (
        await page
          .locator('iframe[src*="captcha"], img[src*="captcha"], [class*="captcha"], [id*="captcha"]')
          .first()
          .isVisible()
          .catch(() => false)
      ) {
        return { success: false, reason: '캡차 노출' };
      }
      const bodyText = await page.locator('body').innerText().catch(() => '');
      if (/차단|보안.*문자|이용.*제한|자동.*입력/.test(bodyText)) {
        return { success: false, reason: '차단/보안문자 키워드 감지' };
      }
      if (/(아이디|비밀번호).*확인|일치하지\s*않|다시\s*입력/.test(bodyText)) {
        return { success: false, reason: '로그인 거부 메시지' };
      }
      return { success: false, reason: '로그인 페이지 미이동' };
    } catch (e) {
      return { success: false, reason: (e as Error).message };
    }
  }

  private async openCart(page: Page): Promise<void> {
    const cartLink = page.getByRole('link', { name: '장바구니' }).first();
    if (await cartLink.isVisible().catch(() => false)) {
      await cartLink.click();
    } else {
      await page.goto('https://www.hmall.com/mo/odb/basktList', { waitUntil: 'domcontentloaded', timeout: 15000 });
    }
    await sleep(rand(1500, 2200));
    await this.dismissOverlays(page);
  }

  async clearCart(): Promise<void> {
    const page = await this.ensurePage();
    await this.openCart(page);

    const generalCheckbox = page.locator('label').filter({ hasText: '일반상품' }).locator('i').first();
    if (!(await generalCheckbox.isVisible().catch(() => false))) {
      // 빈 카트 — 일반상품 라벨 자체가 없음
      return;
    }
    await generalCheckbox.click();
    await sleep(400);

    const deleteBtn = page.getByRole('button', { name: '선택삭제' }).first();
    if (!(await deleteBtn.isVisible().catch(() => false))) return;
    await deleteBtn.click();
    await sleep(rand(500, 900));

    const yesBtn = page.getByRole('button', { name: '예' }).first();
    if (await yesBtn.isVisible().catch(() => false)) {
      await yesBtn.click();
      await sleep(rand(800, 1200));
    }
  }

  async addToCart(code: ProductCode, qty: number): Promise<void> {
    const page = await this.ensurePage();
    const slitmCd = getId(code, 'hyundai');
    const url = `https://www.hmall.com/md/pda/itemPtc?slitmCd=${slitmCd}`;

    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 15000 });
    await sleep(rand(1500, 2200));
    await this.dismissOverlays(page);

    await this.verifyProductPage(code, page, slitmCd);

    const purchaseBtn = page.getByRole('button', { name: '구매하기' }).first();
    try {
      await purchaseBtn.waitFor({ state: 'visible', timeout: 5000 });
    } catch {
      await this.handleExpiredId(code, slitmCd, '구매하기 버튼 미존재');
      return;
    }
    await purchaseBtn.click();
    await sleep(rand(900, 1400));

    // 다중 옵션 페이지: span.choice-num.title 라벨이 있으면 첫 번째 선택
    // (현재 b~h 7종은 단일 옵션이지만, 가이드 부록 호환성 유지)
    const optionLabel = page.locator('span.choice-num.title').first();
    if (await optionLabel.isVisible().catch(() => false)) {
      await optionLabel.click();
      await sleep(400);
    }

    // qty stepper: 초기값 모름 (관측상 0 또는 1) — 읽고 target까지 증가 클릭
    const qtyInput = page.locator('input[name="ordQty"]').first();
    const incBtn = page.getByRole('button', { name: '증가' }).first();
    const readQty = async (): Promise<number> => parseInt((await qtyInput.inputValue().catch(() => '0')) || '0', 10) || 0;
    let cur = await readQty();
    let safety = qty + 8;
    while (cur < qty && safety-- > 0) {
      await incBtn.click();
      await sleep(rand(120, 220));
      cur = await readQty();
    }
    if (cur !== qty) {
      console.log(`  ⚠️ qty 미달: 기대 ${qty}, 실제 ${cur} (code=${code})`);
    }

    // 팝업 내 "장바구니" 버튼 — getByRole은 button만 매치하므로 헤더 link("장바구니")와 충돌 안 함
    const cartBtn = page.getByRole('button', { name: '장바구니' }).first();
    if (!(await cartBtn.isVisible().catch(() => false))) {
      throw new Error(`팝업 내 "장바구니" 버튼을 찾지 못함 (code=${code})`);
    }
    await cartBtn.click();
    await sleep(rand(1500, 2500));
    await this.dismissOverlays(page);
  }

  async applyCoupons(): Promise<void> {
    // 설화수 × 현대Hmall: 카트 레벨 쿠폰 없음 (VIP 라운지 별도 — Phase 2)
    // no-op
  }

  async listCart(): Promise<CartItem[]> {
    const page = await this.ensurePage();
    await this.openCart(page);

    const raw = await page.evaluate(() => {
      const seen = new Set<string>();
      const items: { slitmCd: string; qty: number; priceText: string }[] = [];
      const anchors = Array.from(document.querySelectorAll('a[href*="itemPtc"]'));
      for (const a of anchors) {
        const href = a.getAttribute('href') || '';
        const m = href.match(/slitmCd=(\d+)/);
        if (!m) continue;
        const slitmCd = m[1] || '';
        if (!slitmCd || seen.has(slitmCd)) continue;
        const text = (a.textContent || '').trim();
        const qm = text.match(/(\d+)\s*개/);
        if (!qm) continue;
        const qty = parseInt(qm[1] || '0', 10);
        if (qty < 1) continue;
        seen.add(slitmCd);
        const pm = text.match(/([\d,]+)원/);
        items.push({ slitmCd, qty, priceText: pm ? pm[1] || '' : '' });
      }
      return items;
    });

    // slitmCd → 사내 코드(b~h) → 정식 제품명 매핑
    const result: CartItem[] = [];
    for (const r of raw) {
      let productName = `[unknown slitmCd=${r.slitmCd}]`;
      for (const code of PRODUCT_CODES) {
        try {
          if (getId(code, 'hyundai') === r.slitmCd) {
            productName = PRODUCTS[code].name;
            break;
          }
        } catch {
          /* missing id, skip */
        }
      }
      const price = r.priceText ? parseInt(r.priceText.replace(/,/g, ''), 10) : undefined;
      const item: CartItem = price !== undefined ? { productName, qty: r.qty, price } : { productName, qty: r.qty };
      result.push(item);
    }
    return result;
  }

  async close(): Promise<void> {
    if (this.context) await this.context.close().catch(() => {});
    if (this.browser) await this.browser.close().catch(() => {});
    this.context = null;
    this.browser = null;
    this.page = null;
  }

  private async verifyProductPage(code: ProductCode, page: Page, slitmCd: string): Promise<void> {
    const url = page.url();
    if (url.includes('/error') || url.endsWith('hmall.com/') || url.endsWith('hmall.com')) {
      await this.handleExpiredId(code, slitmCd, `리다이렉트됨: ${url}`);
      return;
    }
    const bodyText = await page.locator('body').innerText().catch(() => '');
    if (/상품을 찾을 수 없|존재하지 않는 상품|판매가 종료/.test(bodyText)) {
      await this.handleExpiredId(code, slitmCd, '상품 없음/종료 메시지 감지');
      return;
    }
    const expected = PRODUCTS[code].name;
    const titleText = await page.locator('h1, h2, .prd-name, [class*="name"]').first().innerText().catch(() => '');
    const norm = (s: string) => s.replace(/\s+/g, '').toLowerCase();
    const expectedKey = norm(expected.split('(')[0] ?? expected).slice(0, 4);
    if (titleText && expectedKey && !norm(titleText).includes(expectedKey)) {
      console.log(`  ⚠️ 제품명 약한 불일치 — 기대 키워드: "${expectedKey}", 페이지 제목: "${titleText.slice(0, 80)}"`);
      // weak signal only — do not block. Strong signals (404 / no purchase btn) are fatal above.
    }
  }

  private async handleExpiredId(code: ProductCode, oldId: string, reason: string): Promise<void> {
    const expected = PRODUCTS[code].name;
    console.log('');
    console.log(`⚠️  제품 '${code} (${expected})'의 현대H몰 ID(slitmCd=${oldId})가 더 이상 유효하지 않습니다.`);
    console.log(`   사유: ${reason}`);
    if (!process.stdin.isTTY) {
      throw new Error(`ID 만료 (비대화 모드) — code=${code}, slitmCd=${oldId}, 사유: ${reason}`);
    }
    console.log(`→ 새 slitmCd 또는 hmall.com URL 입력 (skip / abort 가능):`);

    const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
    const answer = (await rl.question('새 slitmCd: ')).trim();
    rl.close();

    if (answer === 'abort' || answer === '') {
      throw new Error(`Aborted by user — ${code} ID expired`);
    }
    if (answer === 'skip') {
      throw new Error(`Skipped ${code} — combo will be incomplete`);
    }
    const m = answer.match(/slitmCd=(\d+)/);
    const newId = m?.[1] ?? answer.replace(/[^0-9]/g, '');
    if (!newId) throw new Error(`Invalid slitmCd input: ${answer}`);
    setId(code, 'hyundai', newId);
    console.log(`✓ config 갱신 완료: ${code} → ${newId}. 재시도하려면 명령어를 다시 실행하세요.`);
    throw new Error(`ID 갱신됨 (${code}=${newId}) — 명령어 재실행 필요`);
  }
}
