import { Command } from 'commander';
import { runCart } from './commands/sulwhasoo/cart.js';
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
