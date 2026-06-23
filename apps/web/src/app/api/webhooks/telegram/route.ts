export const dynamic = 'force-dynamic'

import { timingSafeEqual } from 'node:crypto'
import type { NextRequest } from 'next/server'
import { publishEnvelope } from '@/lib/qstash'
import { lookupTenant } from '@/lib/redis'

function verifyTelegramToken(incoming: string, secret: string): boolean {
  const a = Buffer.from(incoming)
  const b = Buffer.from(secret)
  if (a.length !== b.length) return false
  return timingSafeEqual(a, b)
}

function isPositiveInt(val: unknown): val is number {
  return typeof val === 'number' && Number.isInteger(val) && val > 0
}

export async function POST(request: NextRequest): Promise<Response> {
  const secret = process.env.TELEGRAM_WEBHOOK_SECRET
  if (!secret) throw new Error('Missing required env: TELEGRAM_WEBHOOK_SECRET')

  // 1. Read raw bytes
  const rawBuffer = await request.arrayBuffer()
  const bodyText = Buffer.from(rawBuffer).toString('utf-8')

  // 2. Constant-time token check (NOT HMAC — api_contracts §1)
  const tokenHeader = request.headers.get('x-telegram-bot-api-secret-token') ?? ''
  if (!tokenHeader || !verifyTelegramToken(tokenHeader, secret)) {
    return new Response('Unauthorized', { status: 401 })
  }

  // 3. Parse body
  let parsed: Record<string, unknown>
  try {
    parsed = JSON.parse(bodyText)
  } catch {
    return new Response('Bad Request: invalid JSON', { status: 400 })
  }

  // 4. update_id validation (api_contracts §5.3.7)
  const updateId: unknown = parsed.update_id
  if (!isPositiveInt(updateId)) {
    return new Response('Bad Request: update_id must be a positive integer', { status: 400 })
  }

  // 5. Telegram user lookup → tenant resolution (api_contracts §7)
  const message = parsed.message as Record<string, unknown> | undefined
  const from = message?.from as Record<string, unknown> | undefined
  const fromId = from?.id

  const tenantId = fromId != null
    ? await lookupTenant(`telegram:user:${fromId}`)
    : null

  // 6. Unknown user → 200 OK (stops Telegram retry loop; token was valid)
  if (!tenantId) {
    return new Response(null, { status: 200 })
  }

  // 7. Publish to QStash (api_contracts §2) — no Postgres write (§0.1)
  await publishEnvelope({
    idempotency_key: String(updateId),
    source: 'telegram',
    raw_payload: bodyText,
    tenant_id: tenantId,
  })

  return new Response(null, { status: 202 })
}
