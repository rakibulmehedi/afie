import { timingSafeEqual } from 'node:crypto'

export function verifyTelegramToken(incoming: string, secret: string): boolean {
  const a = Buffer.from(incoming)
  const b = Buffer.from(secret)
  if (a.length !== b.length) return false
  return timingSafeEqual(a, b)
}

export function isPositiveInt(val: unknown): val is number {
  return typeof val === 'number' && Number.isInteger(val) && val > 0
}
