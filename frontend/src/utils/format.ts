// 数字展示格式化

/** token 数格式化：1234 → "1,234"；1234567 → "1.2M" */
export function fmtTokens(n: number): string {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M'
  if (n >= 100_000) return Math.round(n / 1_000) + 'k'
  return n.toLocaleString()
}

/** 毫秒 → 可读时长 */
export function fmtDuration(ms?: number | null): string {
  if (ms == null) return ''
  if (ms < 1000) return `${ms}ms`
  return (ms / 1000).toFixed(1) + 's'
}
