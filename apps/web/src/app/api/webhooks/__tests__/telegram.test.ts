/**
 * S2.1c — Telegram webhook route tests
 * api_contracts.md §1, §5.3, §7
 * TDD: written BEFORE route implementation exists (RED state)
 */

// env vars are set via jest.setup.env.js (runs before any module loads)

jest.mock('@/lib/messaging/qstash', () => ({
  publishEnvelope: jest.fn(),
}))
jest.mock('@/lib/messaging/redis', () => ({
  lookupTenant: jest.fn(),
}))

import { POST } from '@/app/api/webhooks/telegram/route'
import { publishEnvelope } from '@/lib/messaging/qstash'
import { lookupTenant } from '@/lib/messaging/redis'

// ────────────────────────────────────────────────────────────
// Helpers
// ────────────────────────────────────────────────────────────
const VALID_TENANT = 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11'
const VALID_FROM_ID = 123456789
const VALID_UPDATE_ID = 987654321

function makePayload(overrides: Record<string, unknown> = {}) {
  return JSON.stringify({
    update_id: VALID_UPDATE_ID,
    message: {
      from: { id: VALID_FROM_ID, first_name: 'Test' },
      text: 'hello',
    },
    ...overrides,
  })
}

function makeRequest(body: string, headers: Record<string, string> = {}): Request {
  return new Request('http://localhost/api/webhooks/telegram', {
    method: 'POST',
    body,
    headers: { 'content-type': 'application/json', ...headers },
  })
}

// ────────────────────────────────────────────────────────────
// Tests
// ────────────────────────────────────────────────────────────
describe('Telegram webhook — POST /api/webhooks/telegram', () => {
  const mockPublish = publishEnvelope as jest.MockedFunction<typeof publishEnvelope>
  const mockLookup = lookupTenant as jest.MockedFunction<typeof lookupTenant>

  beforeEach(() => {
    jest.clearAllMocks()
    mockPublish.mockResolvedValue('msg-test-id')
    mockLookup.mockResolvedValue(VALID_TENANT)
  })

  // ── Happy path ──────────────────────────────────────────
  it('valid token and known user returns 202 and calls publish once', async () => {
    const body = makePayload()
    const req = makeRequest(body, {
      'x-telegram-bot-api-secret-token': 'test-telegram-secret',
    })

    const res = await POST(req)

    expect(res.status).toBe(202)
    expect(mockPublish).toHaveBeenCalledTimes(1)
    expect(mockPublish).toHaveBeenCalledWith(
      expect.objectContaining({
        idempotency_key: String(VALID_UPDATE_ID),
        source: 'telegram',
        tenant_id: VALID_TENANT,
      }),
    )
  })

  // ── Token rejections ────────────────────────────────────
  it('missing X-Telegram-Bot-Api-Secret-Token returns 401, no publish', async () => {
    const body = makePayload()
    const req = makeRequest(body)

    const res = await POST(req)

    expect(res.status).toBe(401)
    expect(mockPublish).not.toHaveBeenCalled()
  })

  it('invalid token returns 401, no publish', async () => {
    const body = makePayload()
    const req = makeRequest(body, {
      'x-telegram-bot-api-secret-token': 'wrong-secret',
    })

    const res = await POST(req)

    expect(res.status).toBe(401)
    expect(mockPublish).not.toHaveBeenCalled()
  })

  // ── Unknown user (intentional 200) ─────────────────────
  it('valid token but unknown user returns 200 OK, no publish', async () => {
    mockLookup.mockResolvedValue(null)

    const body = makePayload()
    const req = makeRequest(body, {
      'x-telegram-bot-api-secret-token': 'test-telegram-secret',
    })

    const res = await POST(req)

    expect(res.status).toBe(200)
    expect(mockPublish).not.toHaveBeenCalled()
  })

  // ── update_id validation ────────────────────────────────
  it('update_id as string returns 400, no publish', async () => {
    const body = makePayload({ update_id: 'not-a-number' })
    const req = makeRequest(body, {
      'x-telegram-bot-api-secret-token': 'test-telegram-secret',
    })

    const res = await POST(req)

    expect(res.status).toBe(400)
    expect(mockPublish).not.toHaveBeenCalled()
  })

  it('update_id as float returns 400, no publish', async () => {
    const body = makePayload({ update_id: 1.5 })
    const req = makeRequest(body, {
      'x-telegram-bot-api-secret-token': 'test-telegram-secret',
    })

    const res = await POST(req)

    expect(res.status).toBe(400)
    expect(mockPublish).not.toHaveBeenCalled()
  })

  it('update_id negative returns 400, no publish', async () => {
    const body = makePayload({ update_id: -1 })
    const req = makeRequest(body, {
      'x-telegram-bot-api-secret-token': 'test-telegram-secret',
    })

    const res = await POST(req)

    expect(res.status).toBe(400)
    expect(mockPublish).not.toHaveBeenCalled()
  })

  it('update_id = 0 returns 400, no publish', async () => {
    const body = makePayload({ update_id: 0 })
    const req = makeRequest(body, {
      'x-telegram-bot-api-secret-token': 'test-telegram-secret',
    })

    const res = await POST(req)

    expect(res.status).toBe(400)
    expect(mockPublish).not.toHaveBeenCalled()
  })
})
