import { Client } from '@upstash/qstash'
import type { QStashEnvelope } from '@/types'

export type { QStashEnvelope }

const URL_GROUP = process.env.QSTASH_URL_GROUP ?? 'ingest'

function getClient(): Client {
  const token = process.env.QSTASH_TOKEN
  if (!token) throw new Error('Missing required env: QSTASH_TOKEN')
  return new Client({ token })
}

export async function publishEnvelope(envelope: QStashEnvelope): Promise<string> {
  const result = await getClient().publishJSON({
    urlGroup: URL_GROUP,
    body: envelope,
    deduplicationId: envelope.idempotency_key,
  })
  const first = Array.isArray(result) ? result[0] : result
  return (first as { messageId?: string })?.messageId ?? ''
}
