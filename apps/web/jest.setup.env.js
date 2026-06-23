// Runs before any module loads — sets env for all webhook tests
process.env.GITHUB_WEBHOOK_SECRET = 'test-github-secret'
process.env.TELEGRAM_WEBHOOK_SECRET = 'test-telegram-secret'
process.env.QSTASH_TOKEN = 'test-qstash-token'
process.env.UPSTASH_REDIS_REST_URL = 'https://test.upstash.io'
process.env.UPSTASH_REDIS_REST_TOKEN = 'test-redis-token'
