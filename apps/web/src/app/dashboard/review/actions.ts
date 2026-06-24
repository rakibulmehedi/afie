"use server"

import { Client } from '@upstash/qstash'
import { headers } from 'next/headers'
import { timingSafeEqual } from 'node:crypto'
import { revalidatePath } from 'next/cache'

export async function decideOnDraft(
  draftId: string,
  decision: 'APPROVE' | 'REJECT',
  editedMarkdown?: string,
): Promise<{ ok: boolean; error?: string }> {
  // 1. Auth check
  const hdrs = await headers()
  const incoming = hdrs.get('x-admin-secret') ?? ''
  const secret = process.env.DASHBOARD_ADMIN_SECRET ?? ''

  let authorized = false
  if (incoming.length > 0 && secret.length > 0 && incoming.length === secret.length) {
    try {
      authorized = timingSafeEqual(Buffer.from(incoming), Buffer.from(secret))
    } catch {
      authorized = false
    }
  }

  if (!authorized) {
    return { ok: false, error: 'Unauthorized' }
  }

  // 2. Get tenant
  const tenantId = process.env.DASHBOARD_TENANT_ID
  if (!tenantId) {
    return { ok: false, error: 'Missing DASHBOARD_TENANT_ID' }
  }

  // 3. Build payload
  const payload = {
    draft_id: draftId,
    tenant_id: tenantId,
    decision,
    edited_content: editedMarkdown ?? null,
    actor: 'dashboard',
  }

  // 4. Publish to worker via QStash
  const workerUrl = process.env.QSTASH_WORKER_APPROVE_URL
  if (!workerUrl) throw new Error('Missing QSTASH_WORKER_APPROVE_URL')

  const client = new Client({ token: process.env.QSTASH_TOKEN! })
  await client.publishJSON({ url: workerUrl, body: payload })

  // 5. Revalidate the dashboard page
  revalidatePath('/dashboard/review')

  return { ok: true }
}
