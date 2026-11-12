/** The only place the product name may appear. Everything else reads BRAND_NAME. */
export const BRAND_NAME: string = process.env.NEXT_PUBLIC_BRAND_NAME || 'mashqgoh';

/** Words the server password policy rejects (plus the brand itself). */
export const PLATFORM_WORDS: readonly string[] = [
  'milliy',
  'sertifikat',
  'yuzdanyuz',
  'mock',
  BRAND_NAME.toLowerCase(),
];
