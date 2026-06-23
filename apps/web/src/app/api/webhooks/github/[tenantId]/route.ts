export const dynamic = 'force-dynamic'

import { timingSafeEqual, createHmac } from 'node:crypto'
import type { NextRequest } from 'next/server'
import { publishEnvelope } from '@/lib/qstash'
import { lookupTenant } from '@/lib/redis'

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const FRESHNESS_MS = 5 * 60 * 1000

function verifyGitHubSignature(rawBody: Buffer, sigHeader: string, secret: string): boolean {
  const expected = 'sha256=' + createHmac('sha256', secret).update(rawBody).digest('hex')
  const a = Buffer.from(sigHeader.padEnd(expected.length, '\0'))
  const b = Buffer.from(expected.padEnd(sigHeader.length, '\0'))
  if (a.length !== b.length) return false
  return timingSafeEqual(a, b)
}

function extractTimestamp(body: string): Date | null {
  try {
    const parsed = JSON.parse(body)
    const ts = parsed.created_at ?? parsed.pushed_at ?? parsed.updated_at
    return ts ? new Date(ts) : null
  } catch {
    return null
  }
}

export async function POST(
  request: NextRequest,
  ctx: { params: Promise<{ tenantId: string }> },
): Promise<Response> {
  const secret = process.env.GITHUB_WEBHOOK_SECRET
  if (!secret) throw new Error('Missing required env: GITHUB_WEBHOOK_SECRET')

  const { tenantId } = await ctx.params

  // 1. Read raw bytes
  const rawBuffer = await request.arrayBuffer()
  const rawBody = Buffer.from(rawBuffer)
  const bodyText = rawBody.toString('utf-8')

  // 2. Signature check (api_contracts §1, §5.1)
  const sigHeader = request.headers.get('x-hub-signature-256') ?? ''
  if (!sigHeader || !verifyGitHubSignature(rawBody, sigHeader, secret)) {
    return new Response('Unauthorized', { status: 401 })
  }

  // 3. Delivery UUID validation (api_contracts §5.3.7)
  const deliveryUUID = request.headers.get('x-github-delivery') ?? ''
  if (!UUID_RE.test(deliveryUUID)) {
    return new Response('Bad Request: malformed X-GitHub-Delivery', { status: 400 })
  }

  // 4. Freshness check (api_contracts §5.3.3) — only if payload has a timestamp
  const eventTimestamp = extractTimestamp(bodyText)
  if (eventTimestamp) {
    const age = Date.now() - eventTimestamp.getTime()
    if (Math.abs(age) > FRESHNESS_MS) {
      return new Response('Unauthorized: stale event', { status: 401 })
    }
  }

  // 5. Tenant resolution (api_contracts §7)
  const tenant = await lookupTenant(`tenant:${tenantId}`)
  if (!tenant) {
    return new Response('Unauthorized: unknown tenant', { status: 401 })
  }

  // 6. Publish to QStash (api_contracts §2) — no Postgres write (§0.1)
  await publishEnvelope({
    idempotency_key: deliveryUUID,
    source: 'github',
    raw_payload: bodyText,
    tenant_id: tenantId,
  })

  return new Response(null, { status: 202 })
}
