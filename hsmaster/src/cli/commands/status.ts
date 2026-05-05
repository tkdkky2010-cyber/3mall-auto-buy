import { loadAccounts } from '../../malls/base.js';
import { DAILY_LIMITS, type Track, type Mall } from '../../core/limits.js';
import { getUsageMatrix } from '../../db/state.js';
import { todayKST } from '../../core/kst.js';

interface Slot {
  track: Track;
  mall: Mall;
  limit: number;
  label: string;
}

const SLOTS: Slot[] = [
  { track: 'sulwhasoo',    mall: 'lotte',    limit: 1, label: '설화수롯데' },
  { track: 'sulwhasoo',    mall: 'hyundai',  limit: 3, label: '설화수현대' },
  { track: 'sulwhasoo',    mall: 'galleria', limit: 3, label: '설화수갤러리아' },
  { track: 'hmall_reward', mall: 'hyundai',  limit: 2, label: 'Hmall적립' },
];

export async function runStatus(): Promise<void> {
  const date = todayKST();
  const usage = getUsageMatrix(date);
  const accounts = loadAccounts();

  console.log(`▶ 사용량 매트릭스 (${date} KST)`);
  console.log('');

  const header = ['#', 'ID', ...SLOTS.map((s) => s.label), '여유'];
  console.log(header.join('\t'));
  console.log('─'.repeat(80));

  for (const a of accounts) {
    const cells = SLOTS.map((s) => {
      const used = usage.get(`${a.index}|${s.track}|${s.mall}`) ?? 0;
      const expected = DAILY_LIMITS[s.track][s.mall] ?? 0;
      const mark = used >= expected ? '✗' : ' ';
      return `${used}/${expected}${mark}`;
    });
    const remaining = SLOTS.reduce((sum, s) => {
      const used = usage.get(`${a.index}|${s.track}|${s.mall}`) ?? 0;
      const expected = DAILY_LIMITS[s.track][s.mall] ?? 0;
      return sum + Math.max(0, expected - used);
    }, 0);
    const totalLimit = SLOTS.reduce((sum, s) => sum + (DAILY_LIMITS[s.track][s.mall] ?? 0), 0);
    const flag = remaining === totalLimit ? ' ★' : '';
    console.log([a.index, a.id, ...cells, `${remaining}/${totalLimit}${flag}`].join('\t'));
  }
  console.log('');
  console.log('★ = 오늘 미사용. ✗ = 한도 도달.');
}
