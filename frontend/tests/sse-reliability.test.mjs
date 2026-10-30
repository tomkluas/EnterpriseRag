import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
const source = readFileSync(new URL('../src/utils/sse.js', import.meta.url), 'utf8')
const { SSEClient } = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'))

test('premature EOF is an error instead of a successful empty reply', async () => {
  const original = globalThis.fetch
  globalThis.fetch = async () => new Response('data: {"event":"token","data":"hello"}\n\n')
  try {
    const events = []
    await new SSEClient('/', {}, 'fake').on('error', e => events.push(e)).start()
    assert.equal(events.length, 1)
    assert.match(events[0], /中断|结束|完成/)
  } finally { globalThis.fetch = original }
})

test('CRLF and split UTF-8 SSE frames are parsed and done ends the stream', async () => {
  const original = globalThis.fetch
  const bytes = new TextEncoder().encode('data: {"event":"token","data":"你好"}\r\n\r\ndata: {"event":"done","data":{}}\r\n\r\n')
  globalThis.fetch = async () => new Response(new ReadableStream({ start(c) { c.enqueue(bytes.slice(0, 35)); c.enqueue(bytes.slice(35)); c.close() } }))
  try {
    let text = '', done = false
    await new SSEClient('/', {}, 'fake').on('token', t => text += t).on('done', () => done = true).start()
    assert.equal(text, '你好')
    assert.equal(done, true)
  } finally { globalThis.fetch = original }
})

test('a hung connection times out with a visible error', async () => {
  const original = globalThis.fetch
  globalThis.fetch = (url, { signal }) => new Promise((resolve, reject) => signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError'))))
  try {
    const errors = []
    const client = new SSEClient('/', {}, 'fake', { timeoutMs: 25 })
    const pending = client.on('error', e => errors.push(e)).start()
    await Promise.race([pending, new Promise(resolve => setTimeout(resolve, 100))])
    assert.equal(errors.length, 1, 'hung connection must stop waiting')
    assert.match(errors[0], /超时/)
    client.abort()
    await pending
  } finally { globalThis.fetch = original }
})
