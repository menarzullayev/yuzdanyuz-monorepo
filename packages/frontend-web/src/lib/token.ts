/** Pulls `token` out of `#token=...`. The fragment never reaches server logs or Referer. */
export function tokenFromHash(hash: string): string {
  return new URLSearchParams(hash.replace(/^#/, '')).get('token') ?? '';
}
