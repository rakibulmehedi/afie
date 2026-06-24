export type Platform = 'x' | 'linkedin' | 'telegram'
export type ApprovalDecision = 'APPROVE' | 'REJECT'

export interface DraftRow {
  id: string
  saga_id: string
  persona: string
  target_platform: Platform
  generated_content: string
  created_at: Date | string
  posting_token: string
  deadline_at: Date | string | null
  saga_version: number
}

export interface QStashEnvelope {
  idempotency_key: string
  source: 'github' | 'telegram'
  raw_payload: string
  tenant_id: string
}
