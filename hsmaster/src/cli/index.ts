import { Command } from 'commander';
import { runCart } from './commands/sulwhasoo/cart.js';
import { runAddCart } from './commands/hmall/add-cart.js';
import { runStatus } from './commands/status.js';
import type { Mall } from '../core/limits.js';

const program = new Command();

program
  .name('hsm')
  .description('hsmaster — PRIVATE 홈쇼핑 차익거래 CLI')
  .version('0.1.0');

const sulwhasoo = program.command('sulwhasoo').description('Track A — 설화수 자동구매');

sulwhasoo
  .command('cart')
  .description('11개 조합 중 하나를 장바구니에 담음')
  .requiredOption('--combo <n>', '조합 번호 1~11', (v) => parseInt(v, 10))
  .requiredOption('--mall <name>', 'hyundai | lotte | galleria')
  .option('--account <n>', '계정 인덱스 1~19 (생략 시 자동 선택)', (v) => parseInt(v, 10))
  .option('--dry-run', '실제 클릭 없이 시뮬레이션', false)
  .option('--headless', '헤드리스 모드 (기본 false: 창 보임)', false)
  .action(async (opts: { combo: number; mall: string; account?: number; dryRun: boolean; headless: boolean }) => {
    if (!['hyundai', 'lotte', 'galleria'].includes(opts.mall)) {
      console.error(`Invalid --mall: ${opts.mall}`);
      process.exit(1);
    }
    try {
      await runCart({
        combo: opts.combo,
        mall: opts.mall as Mall,
        account: opts.account,
        dryRun: opts.dryRun,
        headless: opts.headless,
      });
    } catch (e) {
      console.error(`✗ ${(e as Error).message}`);
      process.exit(1);
    }
  });

const hmall = program.command('hmall').description('Track B — Hmall 우수스토어 적립 자동구매');

hmall
  .command('add-cart')
  .description('주어진 상품 URL을 여러 hmall 계정 카트에 담기 (Track B 적립)')
  .requiredOption('--url <url>', 'Hmall 상품 URL (https://www.hmall.com/md/pda/itemPtc?slitmCd=...)')
  .requiredOption('--qty <n>', '계정별 동일 수량 (정수)', (v) => parseInt(v, 10))
  .requiredOption('--accounts <spec>', '계정 범위: "1-19" / "1,3,5" / "all"')
  .option('--clear-cart', '시작 전 카트 비우기', false)
  .option('--dry-run', '실제 카트엔 담지만 사용 카운터 미증가', false)
  .option('--headless', '헤드리스 모드 (기본 false: 창 보임)', false)
  .option('--attach-cdp', 'CDP attach to running Chrome (port 9222) — 가이드 권장 방식 (봇 검출 우회)', false)
  .option('--cdp-port <port>', 'CDP port', (v) => parseInt(v, 10), 9222)
  .action(async (opts: {
    url: string;
    qty: number;
    accounts: string;
    clearCart: boolean;
    dryRun: boolean;
    headless: boolean;
    attachCdp: boolean;
    cdpPort: number;
  }) => {
    try {
      await runAddCart({
        url: opts.url,
        qty: opts.qty,
        accounts: opts.accounts,
        clearCart: opts.clearCart,
        dryRun: opts.dryRun,
        headless: opts.headless,
        persistProfile: !opts.attachCdp, // CDP 사용 시 persistent profile 불필요
        cdpAttach: opts.attachCdp,
        cdpPort: opts.cdpPort,
      });
    } catch (e) {
      console.error(`✗ ${(e as Error).message}`);
      process.exit(1);
    }
  });

program
  .command('status')
  .description('19계정 × 트랙·몰별 오늘 사용량 매트릭스')
  .action(async () => {
    try {
      await runStatus();
    } catch (e) {
      console.error(`✗ ${(e as Error).message}`);
      process.exit(1);
    }
  });

program.parseAsync(process.argv);
