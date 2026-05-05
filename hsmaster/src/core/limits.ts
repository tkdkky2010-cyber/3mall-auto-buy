export type Track = 'sulwhasoo' | 'hmall_reward';
export type Mall = 'hyundai' | 'lotte' | 'galleria';

export const DAILY_LIMITS: Record<Track, Partial<Record<Mall, number>>> = {
  sulwhasoo: { lotte: 1, hyundai: 3, galleria: 3 },
  hmall_reward: { hyundai: 2 },
} as const;

export function limitFor(track: Track, mall: Mall): number {
  const v = DAILY_LIMITS[track][mall];
  if (v === undefined) {
    throw new Error(`No daily limit defined for track=${track} mall=${mall}`);
  }
  return v;
}
