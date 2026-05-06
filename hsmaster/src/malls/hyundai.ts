import { chromium as defaultChromium, type Browser, type BrowserContext, type BrowserType, type Page } from 'playwright';
import * as readline from 'node:readline/promises';
import { mkdirSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import type { Account, CartItem, Mall } from './base.js';
import type { ProductCode } from '../core/sulwhasoo/products.js';
import { PRODUCTS, PRODUCT_CODES } from '../core/sulwhasoo/products.js';
import { getId, setId } from '../core/sulwhasoo/ids.js';
import { getLoginState, recordLoginSuccess, recordLoginFailure } from '../db/state.js';

const __dirname = dirname(fileURLToPath(import.meta.url));
const PROFILES_DIR = resolve(__dirname, '../../../profiles');

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
  /** persistent profile per account: bypass bot detection by reusing user-logged-in cookies */
  persistProfile?: boolean;
  /** CDP attach to user-launched Chrome (port 9222 by default) — guide-recommended approach */
  cdpAttach?: boolean;
  cdpPort?: number;
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
    const launchArgs = ['--disable-blink-features=AutomationControlled'];
    if (level >= 2) launchArgs.push('--incognito');
    // Level 2+ : 시스템 설치된 Chrome 사용 (bundled Chromium보다 fingerprint robust)
    const launchOpts: { headless: boolean | undefined; args: string[]; channel?: string } = {
      headless: this.opts.headless,
      args: launchArgs,
    };
    if (level >= 2) launchOpts.channel = 'chrome';
    this.browser = await browserType.launch(launchOpts);
    // Level 1만 무작위 UA 오버라이드 (Level 2+는 real Chrome 네이티브 UA 사용 — sec-ch-ua 헤더 일관성)
    const ctxOpts: { viewport: { width: number; height: number }; locale: string; userAgent?: string } = {
      viewport: { width: 1280, height: 900 },
      locale: 'ko-KR',
    };
    if (level === 1) {
      const uas = [
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
      ];
      ctxOpts.userAgent = uas[Math.floor(Math.random() * uas.length)];
    }
    this.context = await this.browser.newContext(ctxOpts);
    this.page = await this.context.newPage();
    return this.page;
  }

  async loginIfNeeded(account: Account): Promise<void> {
    if (this.opts.cdpAttach) {
      return this.loginViaCDP(account);
    }
    if (this.opts.persistProfile) {
      return this.loginViaPersistentContext(account);
    }
    return this.loginViaEphemeralFallback(account);
  }

  private async openCDPContext(): Promise<Page> {
    if (this.context && this.page) return this.page;
    const port = this.opts.cdpPort ?? 9222;
    let browser: Browser;
    try {
      browser = await defaultChromium.connectOverCDP(`http://localhost:${port}`);
    } catch (e) {
      throw new Error(
        `CDP 연결 실패 (port ${port}). 먼저 Chrome을 다음 명령으로 띄우세요:\n` +
          `  open -na "Google Chrome" --args --remote-debugging-port=${port} ` +
          `'--remote-allow-origins=*' --user-data-dir="$HOME/HmallChrome"`
      );
    }
    this.browser = browser;
    const ctxs = browser.contexts();
    if (ctxs.length === 0) {
      throw new Error('CDP attach: 실행 중 Chrome에 컨텍스트 없음. Chrome 프로필 확인.');
    }
    this.context = ctxs[0]!;
    this.page = await this.context.newPage();
    this.page.on('dialog', (d) => d.accept().catch(() => {}));
    return this.page;
  }

  private async loginViaCDP(account: Account): Promise<void> {
    const page = await this.openCDPContext();

    // 메인 페이지 진입 → 이미 로그인 상태 확인
    await page.goto('https://www.hmall.com/', { waitUntil: 'domcontentloaded', timeout: 15000 });
    await sleep(2000);

    if (await page.locator('text=로그아웃').first().isVisible().catch(() => false)) {
      // 다른 계정으로 로그인되어 있을 수 있음 — 일관성 위해 logout 후 재로그인
      const logoutLink = page.getByRole('link', { name: '로그아웃' }).first();
      if (await logoutLink.isVisible().catch(() => false)) {
        await logoutLink.click().catch(() => {});
        await sleep(2000);
      }
    }

    // 가이드 E.3: hmall 도메인 쿠키 일괄 삭제
    await page.context().clearCookies({ domain: 'hmall.com' }).catch(() => {});

    // 로그인 폼 진입
    await page.goto('https://www.hmall.com/mo/cob/loginForm', { waitUntil: 'domcontentloaded', timeout: 15000 });
    await sleep(2000);

    // 1차 시도
    let ok = await this._tryLoginCDP(page, account);
    if (ok) {
      console.log(`    ✓ 로그인 성공 (CDP, 1차 시도)`);
      return;
    }

    // 가이드 E.3: deep 정리 후 1회 재시도
    console.log(`    [RETRY] 로그인 차단 감지 — 쿠키/스토리지 폐기 후 재시도`);
    await page.context().clearCookies().catch(() => {});
    await page
      .evaluate(() => {
        try {
          localStorage.clear();
          sessionStorage.clear();
        } catch {
          /* may fail on cross-origin */
        }
      })
      .catch(() => {});
    await sleep(2000);

    await page.goto('https://www.hmall.com/mo/cob/loginForm', { waitUntil: 'domcontentloaded', timeout: 15000 });
    await sleep(2000);

    ok = await this._tryLoginCDP(page, account);
    if (ok) {
      console.log(`    ✓ 로그인 성공 (CDP, 재시도)`);
      return;
    }
    throw new Error(`로그인 차단 (서버 시간 차단 의심) — 5~30분 후 단일 계정 재시도 권장`);
  }

  private async _tryLoginCDP(page: Page, account: Account): Promise<boolean> {
    const idInput = page.locator('input#userid').first();
    try {
      await idInput.waitFor({ state: 'visible', timeout: 5000 });
    } catch {
      return false;
    }
    await idInput.click();
    await idInput.fill(account.id);
    await sleep(1200); // 가이드 E.1: 자동입력 감지 회피

    const pwInput = page.locator('input#password').first();
    await pwInput.click();
    await pwInput.fill(account.pw);
    await sleep(600);

    const loginBtn = page.getByRole('button', { name: '로그인' }).first();
    await loginBtn.click();
    await sleep(3000);

    return await page.locator('text=로그아웃').first().isVisible().catch(() => false);
  }

  private async loginViaPersistentContext(account: Account): Promise<void> {
    const dir = resolve(PROFILES_DIR, `hmall-account-${account.index}`);
    mkdirSync(dir, { recursive: true });

    // close existing
    if (this.context) {
      await this.context.close().catch(() => {});
      this.context = null;
      this.page = null;
    }
    if (this.browser) {
      await this.browser.close().catch(() => {});
      this.browser = null;
    }

    // launchPersistentContext = real Chrome profile, cookies survive reboots
    this.context = await defaultChromium.launchPersistentContext(dir, {
      headless: this.opts.headless ?? false,
      viewport: { width: 1280, height: 900 },
      locale: 'ko-KR',
      channel: 'chrome',
      args: ['--disable-blink-features=AutomationControlled'],
    });
    this.page = this.context.pages()[0] ?? (await this.context.newPage());

    await this.page.goto('https://www.hmall.com/', { waitUntil: 'domcontentloaded', timeout: 15000 });
    await sleep(rand(1500, 2500));

    // 이미 로그인 (저장된 cookie 사용)?
    if (await this.page.locator('text=로그아웃').first().isVisible().catch(() => false)) {
      console.log(`    ✓ 저장된 세션 사용 (재로그인 불필요)`);
      return;
    }

    // Need to login. Navigate to login page + auto-fill, then wait for user click.
    console.log(`    → 로그인 페이지 진입 (자동 입력 후 사용자 클릭 대기)`);
    const loginLink = this.page.getByRole('link', { name: '로그인하기' }).first();
    if (await loginLink.isVisible().catch(() => false)) {
      await loginLink.click();
      await sleep(rand(1500, 2500));
    } else {
      await this.page.goto('https://www.hmall.com/mo/cob/loginForm', { waitUntil: 'domcontentloaded', timeout: 15000 });
      await sleep(rand(1000, 1500));
    }

    const idInput = this.page.locator('input#userid').first();
    try {
      await idInput.waitFor({ state: 'visible', timeout: 5000 });
    } catch {
      throw new Error(`로그인 폼 미발견 (URL=${this.page.url()})`);
    }
    await idInput.click();
    await idInput.pressSequentially(account.id, { delay: rand(50, 150) });
    await sleep(rand(300, 600));

    const pwInput = this.page.locator('input#password').first();
    await pwInput.click();
    await pwInput.pressSequentially(account.pw, { delay: rand(50, 150) });

    console.log(``);
    console.log(`    ⏸  계정 ${account.index} (${maskId(account.id)}) — ID/PW 자동 입력 완료`);
    console.log(`       브라우저의 [로그인] 버튼을 직접 클릭해주세요.`);
    console.log(`       성공 시 자동 진행 (5분 timeout)...`);
    console.log(``);

    const timeoutMs = 5 * 60 * 1000;
    const start = Date.now();
    while (Date.now() - start < timeoutMs) {
      await sleep(1500);
      const url = this.page.url();
      if (/loginForm/.test(url)) continue;
      const ok =
        (await this.page.locator('text=로그아웃').first().isVisible().catch(() => false)) ||
        (await this.page.getByRole('link', { name: '마이페이지' }).first().isVisible().catch(() => false));
      if (!ok) continue;

      // 세션 확정 대기 — redirect chain 끝나고 cookie/session 완전 settle 까지
      console.log(`    · 로그인 감지 — 세션 settle 대기 (3초)...`);
      await sleep(3000);

      // 검증: homepage navigate 후에도 여전히 로그인 상태인지 확인
      try {
        await this.page.goto('https://www.hmall.com/md/dpl/index', { waitUntil: 'domcontentloaded', timeout: 10000 });
        await sleep(1500);
      } catch {
        /* navigation 실패 시 그래도 진행 */
      }
      const stillLoggedIn =
        (await this.page.locator('text=로그아웃').first().isVisible().catch(() => false)) ||
        (await this.page.getByRole('link', { name: '마이페이지' }).first().isVisible().catch(() => false));
      if (stillLoggedIn) {
        console.log(`    ✓ 세션 확정 — 진행 (cookies saved to ${dir.replace(/.*\/profiles\//, 'profiles/')})`);
        return;
      }
      console.log(`    · settle 후 로그인 상태 미유지 — 다시 대기`);
    }
    throw new Error(`로그인 timeout (5분) — 계정 ${account.index} 사용자 클릭 미수행`);
  }

  private async loginViaEphemeralFallback(account: Account): Promise<void> {
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
      // 명시적 cookie/permission clear (newContext 외 추가 보장)
      const ctx = page.context();
      await ctx.clearCookies().catch(() => {});
      await ctx.clearPermissions().catch(() => {});

      // 1) homepage 먼저 방문 (real user flow — direct loginForm URL은 bot 패턴)
      await page.goto('https://www.hmall.com/', { waitUntil: 'domcontentloaded', timeout: 15000 });
      await sleep(rand(1500, 2500));
      await this.dismissOverlays(page);

      // 이미 로그인 상태?
      if (await page.locator('text=로그아웃').first().isVisible().catch(() => false)) {
        return { success: true };
      }

      // 2) homepage GNB의 "로그인하기" link 클릭 → loginForm 진입 (실제 사용자 패턴)
      const loginEntry = page.getByRole('link', { name: '로그인하기' }).first();
      if (await loginEntry.isVisible().catch(() => false)) {
        await loginEntry.click();
        await sleep(rand(1200, 2000));
      } else {
        // fallback: direct URL (homepage link 못 찾는 경우)
        await page.goto('https://www.hmall.com/mo/cob/loginForm', { waitUntil: 'domcontentloaded', timeout: 15000 });
        await sleep(rand(700, 1100));
      }
      await this.dismissOverlays(page);

      // 로그인 폼 렌더 대기
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

      // 로그인 제출: real mouse path + page.mouse.click (CDP)
      // 사람-like preamble: 무작위 영역 hover → 천천히 버튼으로 이동 → 클릭
      const loginBtn = page.getByRole('button', { name: '로그인' }).first();
      await loginBtn.scrollIntoViewIfNeeded().catch(() => {});
      const box = await loginBtn.boundingBox().catch(() => null);
      if (box) {
        // 1) 무작위 영역 첫 위치
        await page.mouse.move(rand(50, 600), rand(100, 500));
        await sleep(rand(300, 600));
        // 2) 페이지 내 무관 좌표 잠시 호버 (실제 사용자처럼 시선 분산)
        await page.mouse.move(rand(100, 800), rand(150, 600), { steps: 8 });
        await sleep(rand(200, 500));
        // 3) 버튼 중앙으로 천천히 이동
        const cx = box.x + box.width / 2 + rand(-3, 3);
        const cy = box.y + box.height / 2 + rand(-3, 3);
        await page.mouse.move(cx, cy, { steps: rand(20, 30) });
        await sleep(rand(400, 800));
        // 4) 클릭 — page.mouse.click 으로 통합 (delay = mousedown→mouseup 사이)
        await page.mouse.click(cx, cy, { delay: rand(60, 130) });
      } else {
        await loginBtn.click({ delay: rand(60, 130) });
      }
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
      if (/로그인.*실패|다른\s*로그인\s*수단/.test(bodyText)) {
        // hmall 봇 차단 — OAuth/휴대폰 로그인 강요. 모든 Level 재시도해도 동일 결과
        return { success: false, reason: 'hmall 차단: 다른 로그인 수단(카카오/네이버/휴대폰) 요구' };
      }
      if (/(아이디|비밀번호).*확인|일치하지\s*않|다시\s*입력/.test(bodyText)) {
        return { success: false, reason: '로그인 거부 메시지' };
      }
      const snippet = bodyText.replace(/\s+/g, ' ').slice(0, 200);
      return { success: false, reason: `로그인 페이지 미이동 (URL=${url}, body="${snippet}")` };
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

    // 가이드 D: 일반상품 체크박스 (label.chklabel 안의 input checkbox)
    const generalCheckbox = page.locator('label.chklabel').filter({ hasText: '일반상품' }).first();
    if (!(await generalCheckbox.isVisible().catch(() => false))) {
      // 빈 카트
      return;
    }
    await generalCheckbox.click();
    await sleep(500);

    // 가이드 D: 선택삭제 (btn-linelgray sm)
    let deleteBtn = page.locator('button.btn-linelgray').filter({ hasText: '선택삭제' }).first();
    if (!(await deleteBtn.isVisible().catch(() => false))) {
      // fallback: getByRole
      deleteBtn = page.getByRole('button', { name: '선택삭제' }).first();
      if (!(await deleteBtn.isVisible().catch(() => false))) return;
    }
    await deleteBtn.click();
    await sleep(rand(700, 1100));

    // 가이드 D: 확인 팝업 — 예 우선, 없으면 확인/삭제 fallback
    for (const name of ['예', '확인', '삭제']) {
      const btn = page.getByRole('button', { name, exact: true }).first();
      if (await btn.isVisible().catch(() => false)) {
        await btn.click().catch(() => {});
        await sleep(rand(800, 1200));
        return;
      }
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
    await this._purchaseFlow(page, qty, code, slitmCd);
  }

  async addToCartByUrl(url: string, qty: number): Promise<void> {
    const page = await this.ensurePage();

    // 최대 2회 시도 — 첫 navigate가 redirect로 떨어지면 1회 재시도
    let lastUrl = '';
    for (let attempt = 1; attempt <= 2; attempt++) {
      await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 15000 });
      await sleep(rand(1500, 2200));
      await this.dismissOverlays(page);
      lastUrl = page.url();
      if (/itemPtc/i.test(lastUrl)) {
        await this._purchaseFlow(page, qty);
        return;
      }
      if (/loginForm/.test(lastUrl) && attempt === 1) {
        console.log(`    · 첫 navigate가 loginForm으로 redirect — 2초 대기 후 재시도`);
        await sleep(2500);
        continue;
      }
      break;
    }
    throw new Error(`상품 페이지 진입 실패 — 최종 URL=${lastUrl}`);
  }

  private async _purchaseFlow(page: Page, qty: number, code?: ProductCode, slitmCdForLog?: string): Promise<void> {
    const purchaseBtn = page.getByRole('button', { name: '구매하기' }).first();
    try {
      await purchaseBtn.waitFor({ state: 'visible', timeout: 5000 });
    } catch {
      if (code && slitmCdForLog) {
        await this.handleExpiredId(code, slitmCdForLog, '구매하기 버튼 미존재');
        return;
      }
      throw new Error(`구매하기 버튼 미발견 — URL=${page.url()}`);
    }
    await purchaseBtn.click();
    await sleep(rand(900, 1400));

    // 다중 옵션 페이지: span.choice-num.title 라벨이 있으면 첫 번째 선택
    const optionLabel = page.locator('span.choice-num.title').first();
    if (await optionLabel.isVisible().catch(() => false)) {
      await optionLabel.click();
      await sleep(400);
    }

    // qty stepper: 읽고 target까지 증가 클릭 (초기값 0/1 무관 robust)
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
      console.log(`  ⚠️ qty 미달: 기대 ${qty}, 실제 ${cur}${code ? ` (code=${code})` : ''}`);
    }

    // 팝업 내 "장바구니" 버튼 — 가이드 B.2: 바로구매 옆 형제 (다른 영역의 btn-cart 회피)
    const cartClicked = await page
      .evaluate(() => {
        const buyNow = Array.from(document.querySelectorAll('button')).find(
          (b) => (b as HTMLElement).offsetParent !== null && b.textContent?.trim() === '바로구매'
        );
        if (!buyNow || !buyNow.parentElement) return false;
        const cart = Array.from(buyNow.parentElement.querySelectorAll('button')).find(
          (b) => b.textContent?.trim() === '장바구니'
        );
        if (!cart) return false;
        (cart as HTMLElement).click();
        return true;
      })
      .catch(() => false);
    if (!cartClicked) {
      // fallback: getByRole
      const cartBtn = page.getByRole('button', { name: '장바구니' }).first();
      if (!(await cartBtn.isVisible().catch(() => false))) {
        throw new Error(`팝업 내 "장바구니" 버튼을 찾지 못함${code ? ` (code=${code})` : ''}`);
      }
      await cartBtn.click();
    }
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
