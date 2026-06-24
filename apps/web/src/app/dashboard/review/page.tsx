import { timingSafeEqual } from 'node:crypto'
import { headers } from 'next/headers'
import { notFound } from 'next/navigation'
import { queryWithTenant } from '@/lib/db/client'
import type { DraftRow } from '@/types'
import DraftCard from './DraftCard'

function toISOString(v: Date | string): string {
  return v instanceof Date ? v.toISOString() : v
}

export default async function ReviewPage() {
  // TODO: replace with NextAuth session before production
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
    notFound()
  }

  const tenantId = process.env.DASHBOARD_TENANT_ID
  if (!tenantId) {
    throw new Error('Missing DASHBOARD_TENANT_ID')
  }

  const drafts = await queryWithTenant<DraftRow>(
    `SELECT d.id, d.saga_id, d.persona, d.target_platform,
            d.generated_content, d.created_at, d.posting_token,
            s.deadline_at, s.version AS saga_version
       FROM cspe_drafts d
       JOIN feature_sagas s ON d.saga_id = s.id
      WHERE d.approval_status = 'PENDING'
        AND s.status = 'AWAITING_APPROVAL'
      ORDER BY d.created_at ASC`,
    [],
    tenantId,
  )

  return (
    <main className="min-h-screen bg-gray-50 p-6">
      <div className="max-w-3xl mx-auto">
        <h1 className="text-xl font-bold text-gray-900 mb-1">Pending Approval</h1>
        <p className="text-sm text-gray-500 mb-6">
          {drafts.length} draft{drafts.length !== 1 ? 's' : ''} awaiting review
        </p>

        {drafts.length === 0 ? (
          <p className="text-gray-500 text-sm">No drafts awaiting approval.</p>
        ) : (
          <div className="space-y-4">
            {drafts.map((draft) => (
              <DraftCard
                key={draft.id}
                id={draft.id}
                sagaId={draft.saga_id}
                persona={draft.persona}
                targetPlatform={draft.target_platform}
                generatedContent={draft.generated_content}
                createdAt={toISOString(draft.created_at)}
                deadlineAt={draft.deadline_at ? toISOString(draft.deadline_at) : null}
                sagaVersion={draft.saga_version}
              />
            ))}
          </div>
        )}
      </div>
    </main>
  )
}
