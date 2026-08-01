export type ProductCode = 'b' | 'c' | 'd' | 'e' | 'f' | 'g' | 'h' | 'n';

export interface SulwhasooProduct {
  code: ProductCode;
  name: string;
  retailPrice: number;
}

export const PRODUCTS: Record<ProductCode, SulwhasooProduct> = {
  b: { code: 'b', name: '윤조3종 (에센셜 퍼스트케어 세트)', retailPrice: 229000 },
  c: { code: 'c', name: '자음2종 (에센셜 데일리 세트)', retailPrice: 150000 },
  d: { code: 'd', name: '본윤2종 (본윤 데일리 루틴 세트/설화수맨)', retailPrice: 125000 },
  e: { code: 'e', name: '탄력3종 (에센셜 탄력케어 세트)', retailPrice: 215000 },
  f: { code: 'f', name: '윤조에센스90 (윤조에센스90ml)', retailPrice: 140000 },
  g: { code: 'g', name: '자음생2종 (자음생 2종 세트)', retailPrice: 225000 },
  h: { code: 'h', name: '자음생크림리치세트 (자음생크림 리치 50ml 기획세트)', retailPrice: 270000 },
  // n = 단품. 재고현황 기존 'n 탄력크림 75ml' 와 같은 SKU (2026-08-01 추가).
  n: { code: 'n', name: '탄력크림EX 75ml ([공통]탄력크림EX 75ml)', retailPrice: 135000 },
} as const;

export const PRODUCT_CODES: readonly ProductCode[] = ['b', 'c', 'd', 'e', 'f', 'g', 'h', 'n'];
