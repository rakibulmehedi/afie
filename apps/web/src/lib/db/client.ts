import { Pool } from 'pg'

let pool: Pool | null = null

function getPool(): Pool {
  if (!pool) {
    const url = process.env.DATABASE_URL
    if (!url) throw new Error('Missing required env: DATABASE_URL')
    pool = new Pool({ connectionString: url, max: 5 })
  }
  return pool
}

// Every dashboard query must run inside a transaction with SET LOCAL app.current_tenant
// so that RLS (Row Level Security) scopes rows to the correct tenant.
// Without SET LOCAL, fail-closed RLS returns zero rows.
export async function queryWithTenant<T>(
  sql: string,
  params: unknown[],
  tenantId: string,
): Promise<T[]> {
  const client = await getPool().connect()
  try {
    await client.query('BEGIN')
    await client.query('SET LOCAL app.current_tenant = $1', [tenantId])
    const result = await client.query(sql, params)
    await client.query('COMMIT')
    return result.rows as T[]
  } catch (err) {
    await client.query('ROLLBACK').catch(() => {}) // best-effort rollback
    throw err
  } finally {
    client.release()
  }
}
