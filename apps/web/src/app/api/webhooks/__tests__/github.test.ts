/**
 * S2.1b — GitHub webhook route tests
 * api_contracts.md §1, §5.1, §5.3, §7
 * TDD: written BEFORE route implementation exists (RED state)
 */

import crypto from 'node:crypto'

// env vars are set via jest.setup.env.js (runs before any module loads)

// Mock lib modules with factory
jest.mock('@/lib/qstash', () => ({
  publishEnvelope: jest.fn(),
}))
jest.mock('@/lib/redis', () => ({
  lookupTenant: jest.fn(),
}))

// Deferred imports — resolved after mocks are set up
import { POST } from '@/app/api/webhooks/github/[tenantId]/route'
import { publishEnvelope } from '@/lib/qstash'
import { lookupTenant } from '@/lib/redis'

// ────────────────────────────────────────────────────────────
// Helpers
// ────────────────────────────────────────────────────────────
const VALID_UUID = '550e8400-e29b-41d4-a716-446655440000'
const VALID_TENANT = 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11'

function sign(body: string, secret: string): string {
  const hmac = crypto.createHmac('sha256', secret)
  hmac.update(body)
  return 'sha256=' + hmac.digest('hex')
}

function makeRequest(
  body: string,
  headers: Record<string, string> = {},
): Request {
  return new Request(`http://localhost/api/webhooks/github/${VALID_TENANT}`, {
    method: 'POST',
    body,
    headers: { 'content-type': 'application/json', ...headers },
  })
}

function routeParams() {
  return { params: Promise.resolve({ tenantId: VALID_TENANT }) }
}

// ────────────────────────────────────────────────────────────
// Tests
// ────────────────────────────────────────────────────────────
describe('GitHub webhook — POST /api/webhooks/github/[tenantId]', () => {
  const mockPublish = publishEnvelope as jest.MockedFunction<typeof publishEnvelope>
  const mockLookup = lookupTenant as jest.MockedFunction<typeof lookupTenant>

  beforeEach(() => {
    jest.clearAllMocks()
    mockPublish.mockResolvedValue('msg-test-id')
    mockLookup.mockResolvedValue(VALID_TENANT)
  })

  // ── Happy path ──────────────────────────────────────────
  it('valid signature returns 202 and calls publish once', async () => {
    const body = JSON.stringify({ repository: { full_name: 'owner/repo' } })
    const sig = sign(body, 'test-github-secret')
    const req = makeRequest(body, {
      'x-hub-signature-256': sig,
      'x-github-delivery': VALID_UUID,
      'x-github-event': 'push',
    })

    const res = await POST(req, routeParams())

    expect(res.status).toBe(202)
    expect(mockPublish).toHaveBeenCalledTimes(1)
    expect(mockPublish).toHaveBeenCalledWith(
      expect.objectContaining({
        idempotency_key: VALID_UUID,
        source: 'github',
        tenant_id: VALID_TENANT,
      }),
    )
  })

  // ── Signature rejections ────────────────────────────────
  it('missing X-Hub-Signature-256 returns 401, no publish', async () => {
    const body = JSON.stringify({ repository: { full_name: 'owner/repo' } })
    const req = makeRequest(body, {
      'x-github-delivery': VALID_UUID,
      'x-github-event': 'push',
    })

    const res = await POST(req, routeParams())

    expect(res.status).toBe(401)
    expect(mockPublish).not.toHaveBeenCalled()
  })

  it('invalid signature (tampered body) returns 401, no publish', async () => {
    const realBody = JSON.stringify({ repository: { full_name: 'owner/repo' } })
    const tamperedBody = JSON.stringify({ repository: { full_name: 'evil/repo' } })
    const sig = sign(realBody, 'test-github-secret')
    const req = makeRequest(tamperedBody, {
      'x-hub-signature-256': sig,
      'x-github-delivery': VALID_UUID,
      'x-github-event': 'push',
    })

    const res = await POST(req, routeParams())

    expect(res.status).toBe(401)
    expect(mockPublish).not.toHaveBeenCalled()
  })

  // ── Idempotency key validation ──────────────────────────
  it('malformed X-GitHub-Delivery (not UUID) returns 400, no publish', async () => {
    const body = JSON.stringify({ repository: { full_name: 'owner/repo' } })
    const sig = sign(body, 'test-github-secret')
    const req = makeRequest(body, {
      'x-hub-signature-256': sig,
      'x-github-delivery': 'not-a-uuid',
      'x-github-event': 'push',
    })

    const res = await POST(req, routeParams())

    expect(res.status).toBe(400)
    expect(mockPublish).not.toHaveBeenCalled()
  })

  // ── Freshness check ─────────────────────────────────────
  it('stale created_at (>5 min ago) returns 401, no publish', async () => {
    const staleTime = new Date(Date.now() - 10 * 60 * 1000).toISOString()
    const body = JSON.stringify({
      created_at: staleTime,
      repository: { full_name: 'owner/repo' },
    })
    const sig = sign(body, 'test-github-secret')
    const req = makeRequest(body, {
      'x-hub-signature-256': sig,
      'x-github-delivery': VALID_UUID,
      'x-github-event': 'create',
    })

    const res = await POST(req, routeParams())

    expect(res.status).toBe(401)
    expect(mockPublish).not.toHaveBeenCalled()
  })

  // ── Tenant resolution ───────────────────────────────────
  it('unresolvable tenantId returns 401, no publish', async () => {
    mockLookup.mockResolvedValue(null)

    const body = JSON.stringify({ repository: { full_name: 'owner/repo' } })
    const sig = sign(body, 'test-github-secret')
    const req = makeRequest(body, {
      'x-hub-signature-256': sig,
      'x-github-delivery': VALID_UUID,
      'x-github-event': 'push',
    })

    const res = await POST(req, routeParams())

    expect(res.status).toBe(401)
    expect(mockPublish).not.toHaveBeenCalled()
  })
})
