import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

const errors = await readFile(new URL('../src/utils/errors.js', import.meta.url), 'utf8')
const source = (await readFile(new URL('../src/utils/sse.js', import.meta.url), 'utf8')).replace("import { errorMessage } from './errors.js'", errors.replace('export function', 'function'))
const { SSEClient } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`)

async function run(response) {
  const original = globalThis.fetch
  globalThis.fetch = async () => { if (response instanceof Error) throw response; return response }
  const events = []
  const client = new SSEClient('/stream', {}, 'token')
  for (const event of ['token', 'done', 'error', 'close', 'unauthorized']) client.on(event, data => events.push([event, data]))
  try { await client.start(); return events } finally { globalThis.fetch = original }
}

test('parses CRLF and an unterminated final event', async () => {
  const events = await run(new Response('data: {"event":"token","data":"你好"}\r\n\r\ndata:{"event":"done","data":{}}'))
  assert.deepEqual(events.map(e => e[0]), ['token', 'done', 'close'])
  assert.equal(events[0][1], '你好')
})
test('network failure reports an error and always closes', async () => {
  assert.deepEqual((await run(new Error('offline'))).map(e => e[0]), ['error', 'close'])
})
test('abort closes without showing a request failure', async () => {
  assert.deepEqual((await run(new DOMException('stopped', 'AbortError'))).map(e => e[0]), ['close'])
})
test('unexpected EOF reports interrupted answer', async () => {
  assert.deepEqual((await run(new Response(''))).map(e => e[0]), ['error', 'close'])
})
test('HTTP 401 normalizes structured errors and signals expiration', async () => {
  const events = await run(new Response(JSON.stringify({detail: {message: 'expired'}}), {status: 401}))
  assert.deepEqual(events.map(e => e[0]), ['unauthorized', 'error', 'close'])
  assert.equal(events[1][1], 'expired')
})
test('stream error objects display a readable message', async () => {
  const events = await run(new Response('data: {"event":"error","data":{"message":"no model"}}\n\n'))
  assert.equal(events[0][1], 'no model')
  assert.deepEqual(events.map(e => e[0]), ['error', 'close'])
})

