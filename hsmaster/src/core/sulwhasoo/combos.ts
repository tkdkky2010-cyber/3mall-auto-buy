import type { ProductCode } from './products.js';

export interface ComboItem {
  code: ProductCode;
  qty: number;
}

export interface SulwhasooCombo {
  id: number;
  items: ComboItem[];
}

export const COMBOS: SulwhasooCombo[] = [
  { id: 1,  items: [{ code: 'g', qty: 2 }, { code: 'h', qty: 1 }] },
  { id: 2,  items: [{ code: 'd', qty: 2 }, { code: 'g', qty: 2 }] },
  { id: 3,  items: [{ code: 'd', qty: 4 }, { code: 'e', qty: 1 }] },
  { id: 4,  items: [{ code: 'e', qty: 2 }, { code: 'h', qty: 1 }] },
  { id: 5,  items: [{ code: 'b', qty: 2 }, { code: 'd', qty: 2 }] },
  { id: 6,  items: [{ code: 'e', qty: 2 }, { code: 'f', qty: 2 }] },
  { id: 7,  items: [{ code: 'c', qty: 3 }, { code: 'h', qty: 1 }] },
  { id: 8,  items: [{ code: 'c', qty: 3 }, { code: 'd', qty: 2 }] },
  { id: 9,  items: [{ code: 'c', qty: 1 }, { code: 'f', qty: 4 }] },
  { id: 10, items: [{ code: 'c', qty: 2 }, { code: 'f', qty: 3 }] },
  { id: 11, items: [{ code: 'f', qty: 5 }] },
];

export function getCombo(id: number): SulwhasooCombo {
  const c = COMBOS.find((x) => x.id === id);
  if (!c) throw new Error(`Unknown combo id: ${id} (valid: 1~11)`);
  return c;
}
