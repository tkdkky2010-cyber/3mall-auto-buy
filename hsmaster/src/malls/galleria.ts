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

const PRODUCT_URL = (goodsNo: string): string =>
  `https://www.galleria.co.kr/goods/initDetailGoods.action?goods_no=${goodsNo}`;

export interface GalleriaOptions {
  headless?: boolean;
  dryRun?: boolean;
}

export class GalleriaMall implements Mall {
  private browser: Browser | null = null;
  private context: BrowserContext | null = null;
  private page: Page | null = null;
  private opts: GalleriaOptions;

  constructor(opts: GalleriaOptions = {}) {
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
    this.page.on('dialog', (d) => d.accept().catch(() => {}));
    return this.page;
  }

  private async dismissOverlays(page: Page): Promise<void> {
    // 1) "다시 보지 않기" 버튼 빠르게 클릭
    for (const loc of [
      page.getByRole('button', { name: '다시 보지 않기' }).first(),
      page.getByRole('button', { name: /레이어 닫기/ }).first(),
    ]) {
      if (await loc.isVisible().catch(() => false)) {
        await loc.click().catch(() => {});
        await sleep(120);
      }
    }
    // 2) groobee 광고 dimmer / .grbDim 잔류 element를 DOM에서 제거 (click 가로채기 방지)
    await page
      .evaluate(() => {
        const sels = ['.grbDim', '.grbLayer', '[class^="groobeeWrapper_"]', '[class*="groobee"]'];
        for (const sel of sels) {
          document.querySelectorAll(sel).forEach((el) => el.remove());
        }
      })
      .catch(() => {});
  }

  async loginIfNeeded(account: Account): Promise<void> {
    const state = getLoginState(account.index, 'galleria');
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
        recordLoginSuccess(account.index, 'galleria', level);
        if (level > 1) console.log(`  ✓ 로그인 성공 (Level ${level})`);
        return;
      }
      lastReason = result.reason ?? 'unknown';
      console.log(`  ✗ Level ${level} 실패: ${lastReason}`);
    }
    const total = recordLoginFailure(account.index, 'galleria');
    throw new Error(
      `계정 ${account.index} (${maskId(account.id)}) 봇 차단 추정 — Level ${levels.join('→')} 전부 실패. 누적 ${total}회. IP 변경 또는 30분 후 재시도 권장. 마지막: ${lastReason}`
    );
  }

  private async attemptLogin(page: Page, account: Account, level: Level): Promise<{ success: boolean; reason?: string }> {
    try {
      await page.goto('https://www.galleria.co.kr/main/initMain.action', {
        waitUntil: 'domcontentloaded',
        timeout: 15000,
      });
      await sleep(rand(1000, 1500));
      await this.dismissOverlays(page);

      // 이미 로그인 상태?
      if (await page.locator('text=로그아웃').first().isVisible().catch(() => false)) {
        return { success: true };
      }

      // 헤더 "로그인" link 클릭 → 로그인 layer 진입
      const loginLink = page.getByRole('link', { name: '로그인' }).first();
      try {
        await loginLink.waitFor({ state: 'visible', timeout: 5000 });
      } catch {
        return { success: false, reason: '로그인 link 미발견' };
      }
      await loginLink.click();
      await sleep(rand(800, 1200));

      // Step 1: ID 입력 → 다음
      const idInput = page.getByRole('textbox', { name: '아이디 또는 이메일' }).first();
      try {
        await idInput.waitFor({ state: 'visible', timeout: 5000 });
      } catch {
        return { success: false, reason: 'ID input 미발견' };
      }
      const charDelay = (): number => rand(50, 150);
      const gapMin = level >= 2 ? 700 : 300;
      const gapMax = level >= 2 ? 1500 : 700;
      await idInput.click();
      await idInput.pressSequentially(account.id, { delay: charDelay() });
      await sleep(rand(gapMin, gapMax));
      const nextBtn = page.getByRole('button', { name: '다음' }).first();
      await nextBtn.click();
      await sleep(rand(800, 1200));

      // Step 2: PW 입력 → 로그인
      const pwInput = page.getByRole('textbox', { name: '비밀번호' }).first();
      try {
        await pwInput.waitFor({ state: 'visible', timeout: 5000 });
      } catch {
        return { success: false, reason: 'PW input 미발견 (ID step 실패 가능)' };
      }
      await pwInput.click();
      await pwInput.pressSequentially(account.pw, { delay: charDelay() });
      await sleep(rand(gapMin, gapMax));
      const submitBtn = page.getByRole('button', { name: '로그인', exact: true }).first();
      await submitBtn.click();
      await sleep(5000);
      await this.dismissOverlays(page);

      // 로그인 성공 검사
      if (await page.locator('text=로그아웃').first().isVisible().catch(() => false)) {
        return { success: true };
      }
      const loginLinkStill = await page.getByRole('link', { name: '로그인' }).first().isVisible().catch(() => false);
      if (!loginLinkStill) return { success: true };

      // 차단 / 거부 진단
      const body = await page.locator('body').innerText().catch(() => '');
      if (
        await page
          .locator('iframe[src*="captcha"], img[src*="captcha"], [class*="captcha"]')
          .first()
          .isVisible()
          .catch(() => false)
      ) {
        return { success: false, reason: '캡차 노출' };
      }
      if (/차단|보안.*문자|이용.*제한/.test(body)) {
        return { success: false, reason: '차단 키워드 감지' };
      }
      if (/(아이디|비밀번호).*확인|일치하지\s*않/.test(body)) {
        return { success: false, reason: '로그인 거부 메시지' };
      }
      return { success: false, reason: '로그인 link 여전히 표시됨' };
    } catch (e) {
      return { success: false, reason: (e as Error).message };
    }
  }

  private async openCart(page: Page): Promise<void> {
    const cartLink = page.getByRole('link', { name: /장바구니/ }).first();
    if (await cartLink.isVisible().catch(() => false)) {
      await cartLink.click();
    } else {
      await page.goto('https://www.galleria.co.kr/cart/cart.action', {
        waitUntil: 'domcontentloaded',
        timeout: 15000,
      });
    }
    await sleep(rand(1500, 2200));
    await this.dismissOverlays(page);
  }

  async clearCart(): Promise<void> {
    for (let attempt = 1; attempt <= 3; attempt++) {
      const page = await this.ensurePage();
      await this.openCart(page);

      // 전체선택 버튼 — 있으면 카트에 뭐가 있다는 뜻, 없으면 비어있음
      const selectAll = page.getByText('전체선택', { exact: true }).first();
      const hasSelectAll = await selectAll.isVisible().catch(() => false);
      if (!hasSelectAll) return;
      if (attempt > 1) console.log(`        clearCart retry ${attempt}: 잔여 있음 → 재시도`);

      await selectAll.click().catch(() => {});
      await sleep(rand(400, 700));

      const deleteBtn = page.getByRole('button', { name: '삭제', exact: true }).first();
      if (!(await deleteBtn.isVisible().catch(() => false))) return;
      await deleteBtn.click();
      await sleep(rand(800, 1200));

      // 확인 모달 (native dialog는 page-level handler accept)
      for (const loc of [
        page.getByRole('button', { name: '예' }).first(),
        page.getByRole('button', { name: '확인', exact: true }).first(),
        page.getByRole('link', { name: '예' }).first(),
        page.getByRole('link', { name: '확인' }).first(),
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
    console.log(`        ⚠️ clearCart: 3회 시도 후에도 비우지 못함`);
  }

  async addToCart(code: ProductCode, qty: number): Promise<void> {
    const page = await this.ensurePage();
    const goodsNo = getId(code, 'galleria');
    const url = PRODUCT_URL(goodsNo);
    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 15000 });
    await sleep(rand(1500, 2500));
    await this.dismissOverlays(page);

    await this.verifyProductPage(code, page, goodsNo);

    // 쿠폰 다운로드 (best effort — 동적 텍스트 트리거)
    await this.collectCoupons(page);

    // qty 증가 — #gds_sltd 안의 "상품수량증가" 버튼 (qty-1)회 클릭
    if (qty > 1) {
      const incBtn = page.locator('#gds_sltd').getByRole('button', { name: '상품수량증가' }).first();
      try {
        await incBtn.waitFor({ state: 'visible', timeout: 3000 });
      } catch {
        console.log(`        ⚠️ 수량증가 버튼 미발견, qty 1로 진행 (code=${code})`);
      }
      for (let i = 1; i < qty; i++) {
        await incBtn.click().catch(() => {});
        await sleep(rand(150, 280));
      }
    }

    // 장바구니
    const cartBtn = page.getByRole('button', { name: '장바구니 상품담기 레이어 열기' }).first();
    try {
      await cartBtn.waitFor({ state: 'visible', timeout: 5000 });
    } catch {
      throw new Error(`장바구니 상품담기 버튼 미발견 (code=${code}, URL=${page.url()})`);
    }
    await cartBtn.click({ force: true }).catch(async () => {
      // force click 실패 시 한 번 더 dismiss + 재시도
      await this.dismissOverlays(page);
      await cartBtn.click({ force: true });
    });
    await sleep(rand(1000, 1500));

    // 동의 (이용약관 등 — 처음 1회만 노출 가능)
    const agreeBtn = page.getByRole('button', { name: '동의' }).first();
    if (await agreeBtn.isVisible().catch(() => false)) {
      await agreeBtn.click().catch(() => {});
      await sleep(rand(500, 800));
    }

    // 계속 쇼핑 (담기 완료 모달 닫기)
    const continueBtn = page.getByRole('button', { name: '계속 쇼핑' }).first();
    if (await continueBtn.isVisible().catch(() => false)) {
      await continueBtn.click().catch(() => {});
      await sleep(rand(500, 800));
    }
    await this.dismissOverlays(page);
  }

  private async collectCoupons(page: Page): Promise<void> {
    // 갤러리아 쿠폰 트리거 버튼은 텍스트가 동적 (예: "[화장] 설화수 더블 18%")
    // heuristic: "쿠폰" 또는 "다운로드" 관련 button 시도, 모든 쿠폰 다운로드 패널 등장 여부로 검증
    const couponBtnCandidates = [
      page.locator('button:has-text("쿠폰")'),
      page.locator('button:has-text("다운로드")'),
      page.locator('button:has-text("할인")').filter({ hasText: '%' }),
    ];
    for (const cands of couponBtnCandidates) {
      const count = await cands.count().catch(() => 0);
      for (let i = 0; i < Math.min(count, 5); i++) {
        const btn = cands.nth(i);
        if (!(await btn.isVisible().catch(() => false))) continue;
        await btn.click().catch(() => {});
        await sleep(rand(500, 800));
        const dlBtn = page.getByRole('button', { name: '모든 쿠폰 다운로드' }).first();
        if (await dlBtn.isVisible().catch(() => false)) {
          await dlBtn.click().catch(() => {});
          await sleep(rand(500, 800));
          const okBtn = page.getByRole('button', { name: '확인', exact: true }).first();
          if (await okBtn.isVisible().catch(() => false)) {
            await okBtn.click().catch(() => {});
            await sleep(300);
          }
          const closeBtn = page.getByRole('button', { name: /레이어 닫기/ }).first();
          if (await closeBtn.isVisible().catch(() => false)) {
            await closeBtn.click().catch(() => {});
            await sleep(300);
          }
          return;
        }
        // wrong popup — close any open layer
        const close = page.getByRole('button', { name: /닫기/ }).first();
        if (await close.isVisible().catch(() => false)) {
          await close.click().catch(() => {});
          await sleep(300);
        }
      }
    }
  }

  async applyCoupons(): Promise<void> {
    // 갤러리아: 쿠폰은 addToCart 시점에 받음 → 카트 단계 no-op
  }

  async listCart(): Promise<CartItem[]> {
    const page = await this.ensurePage();
    await this.openCart(page);
    // AJAX/lazy-load 대비 추가 대기
    await sleep(2000);

    // 본윤/탄력/설화수 텍스트로 row 추정 (DOM selector 다양 — 텍스트 매칭이 안전)
    const productRowCount = await page.evaluate(() => {
      const allEls = Array.from(document.querySelectorAll('*'));
      let count = 0;
      for (const el of allEls) {
        const ownText = (el.textContent || '').replace(/\s+/g, ' ').trim();
        if (!ownText || ownText.length > 200) continue;
        if (el.children.length > 5) continue;
        if (/본윤|탄력|설화수.*세트/.test(ownText)) count++;
      }
      return count;
    });

    // 전체선택 후 상품금액(소비자가 합계) 읽기
    const selectAll = page.getByText('전체선택', { exact: true }).first();
    if (await selectAll.isVisible().catch(() => false)) {
      await selectAll.click().catch(() => {});
      await sleep(rand(600, 1000));
    }

    // 상품금액 합계 읽기 — th[scope="row"] / 텍스트 매치 fallback
    const total = await page.evaluate(() => {
      // 1차: th[scope="row"] 패턴 (lotte와 동일 시도)
      const ths = Array.from(document.querySelectorAll('th[scope="row"], th, dt, .label'));
      for (const th of ths) {
        const t = (th.textContent || '').trim();
        if (t === '상품금액' || t === '총 상품금액' || t.includes('상품금액 합계')) {
          const tr = th.closest('tr');
          const td =
            tr?.querySelector('td') ||
            (th.nextElementSibling as HTMLElement | null) ||
            th.parentElement?.querySelector('.value, .amount, dd');
          const text = (td?.textContent || '').trim();
          const m = text.match(/([\d,]+)\s*원/);
          if (m && m[1]) return parseInt(m[1].replace(/,/g, ''), 10);
        }
      }
      // 2차: 본문 텍스트에서 "상품금액" 근처 숫자 추출
      const bodyText = document.body.innerText;
      const m = bodyText.match(/상품금액[^\d]{0,30}([\d,]+)\s*원/);
      if (m && m[1]) return parseInt(m[1].replace(/,/g, ''), 10);
      return null;
    });

    if (total === null) {
      if (productRowCount === 0) return [];
      return [{ productName: `[galleria 카트 ${productRowCount}건 — 상품금액 읽기 실패]`, qty: productRowCount }];
    }
    const label = productRowCount > 0 ? `[galleria 카트 ${productRowCount}건 합계]` : `[galleria 카트 합계 (row 셀렉터 미매칭)]`;
    return [{ productName: label, qty: 1, price: total }];
  }

  async close(): Promise<void> {
    if (this.context) await this.context.close().catch(() => {});
    if (this.browser) await this.browser.close().catch(() => {});
    this.context = null;
    this.browser = null;
    this.page = null;
  }

  private async verifyProductPage(code: ProductCode, page: Page, goodsNo: string): Promise<void> {
    const url = page.url();
    if (!url.includes('initDetailGoods') || !url.includes(goodsNo)) {
      await this.handleExpiredId(code, goodsNo, `URL 리다이렉트: ${url}`);
      return;
    }
    const bodyText = await page.locator('body').innerText().catch(() => '');
    if (/상품을 찾을 수 없|존재하지 않는 상품|판매가 종료|판매중지/.test(bodyText)) {
      await this.handleExpiredId(code, goodsNo, '상품 없음/종료 메시지 감지');
      return;
    }
    const hasCartBtn = await page
      .getByRole('button', { name: '장바구니 상품담기 레이어 열기' })
      .first()
      .isVisible()
      .catch(() => false);
    if (!hasCartBtn) {
      await this.handleExpiredId(code, goodsNo, '장바구니 버튼 미노출');
      return;
    }
  }

  private async handleExpiredId(code: ProductCode, oldGoodsNo: string, reason: string): Promise<void> {
    const expected = PRODUCTS[code].name;
    console.log('');
    console.log(`⚠️  제품 '${code} (${expected})'의 galleria goods_no=${oldGoodsNo}가 더 이상 유효하지 않습니다.`);
    console.log(`   사유: ${reason}`);
    if (!process.stdin.isTTY) {
      throw new Error(`goods_no 만료 (비대화 모드) — code=${code}, goods_no=${oldGoodsNo}, 사유: ${reason}`);
    }
    console.log(`→ 새 goods_no 또는 galleria URL 입력 (skip / abort 가능):`);
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
    setId(code, 'galleria', newId);
    console.log(`✓ config 갱신 완료: ${code} → ${newId}. 재시도하려면 명령어를 다시 실행하세요.`);
    throw new Error(`goods_no 갱신됨 (${code}=${newId}) — 명령어 재실행 필요`);
  }
}

// PRODUCT_CODES referenced for type completeness even when unused at runtime
void PRODUCT_CODES;
