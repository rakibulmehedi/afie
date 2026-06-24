import { timingSafeEqual, createHmac } from 'node:crypto'

export const FRESHNESS_MS = 5 * 60 * 1000

export function verifyGitHubSignature(rawBody: Buffer, sigHeader: string, secret: string): boolean {
  const expected = 'sha256=' + createHmac('sha256', secret).update(rawBody).digest('hex')
  const a = Buffer.from(sigHeader.padEnd(expected.length, '\0'))
  const b = Buffer.from(expected.padEnd(sigHeader.length, '\0'))
  if (a.length !== b.length) return false
  return timingSafeEqual(a, b)
}

export function extractTimestamp(body: string): Date | null {
  try {
    const parsed = JSON.parse(body)
    const ts = parsed.created_at ?? parsed.pushed_at ?? parsed.updated_at
    return ts ? new Date(ts) : null
  } catch {
    return null
  }
}

export function isValidDeliveryUUID(id: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id)
}
