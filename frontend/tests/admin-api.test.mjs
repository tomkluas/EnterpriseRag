import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

const source = (await readFile(new URL('../src/api/admin.js', import.meta.url), 'utf8'))
  .replace("import http from './http'", 'const http = { post: async (...args) => args }')
const api = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`)

test('sensitive admin creation payloads use JSON bodies without query parameters', async () => {
  const model = { name: 'test', provider: 'openai', model_string: 'model', api_key: 'test-key' }
  assert.deepEqual(await api.createLLMConfig(model), ['/v1/admin/llm-configs', model])
  const database = { name: 'test', password: 'test-password' }
  assert.deepEqual(await api.createDBConnection(database), ['/v1/admin/db-connections', database])
  assert.deepEqual(await api.createUser('user', 'test-password', 'user'),
    ['/v1/admin/users', { username: 'user', password: 'test-password', role: 'user' }])
})
