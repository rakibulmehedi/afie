import { Redis } from '@upstash/redis'

function getClient(): Redis {
  const url = process.env.UPSTASH_REDIS_REST_URL
  const token = process.env.UPSTASH_REDIS_REST_TOKEN
  if (!url) throw new Error('Missing required env: UPSTASH_REDIS_REST_URL')
  if (!token) throw new Error('Missing required env: UPSTASH_REDIS_REST_TOKEN')
  return new Redis({ url, token })
}

/**
 * Generic tenant lookup from Redis.
 *
 * Keys:
 *  - GitHub:   tenant:{tenantId}        → any truthy string (tenant exists)
 *  - Telegram: telegram:user:{fromId}   → tenant UUID
 */
export async function lookupTenant(key: string): Promise<string | null> {
  const value = await getClient().get<string>(key)
  return value ?? null
}
