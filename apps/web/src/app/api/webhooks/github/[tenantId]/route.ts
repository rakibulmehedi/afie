export const dynamic = 'force-dynamic'

import type { NextRequest } from 'next/server'
import { publishEnvelope } from '@/lib/messaging/qstash'
import { lookupTenant } from '@/lib/messaging/redis'
import {
  verifyGitHubSignature,
  extractTimestamp,
  isValidDeliveryUUID,
  FRESHNESS_MS,
} from '@/lib/webhooks/github'

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
  if (!isValidDeliveryUUID(deliveryUUID)) {
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
