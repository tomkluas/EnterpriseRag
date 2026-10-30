import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import vm from 'node:vm'

async function store() {
  const source = await readFile(new URL('../src/stores/chat.js', import.meta.url), 'utf8')
  let id = 0
  return vm.runInNewContext(source.replace(/^import .*$/gm, '').replace('export const useChatStore', 'const useChatStore') + '\nuseChatStore()', {
    defineStore: (_, factory) => factory, ref: value => ({ value }), uuidv4: () => String(++id),
  })
}

test('stream updates original conversation after switching sessions', async () => {
  const chat = await store()
  chat.newSession()
  const first = chat.currentSession()
  const msg = chat.addMessage({role: 'assistant'})
  chat.newSession()
  chat.appendToken(msg, 'answer')
  chat.addSource(msg, {source_name: 'doc', source_detail: '1'})
  assert.equal(first.messages[0].content, 'answer')
  assert.equal(first.messages[0].sources.length, 1)
  assert.equal(chat.currentSession().messages.length, 0)
})

test('logout cleanup removes conversations and ignores late stream updates', async () => {
  const chat = await store()
  chat.newSession()
  const id = chat.addMessage({role:'assistant'})
  chat.clearSessions()
  chat.appendToken(id, 'late token')
  assert.equal(chat.sessions.value.length, 0)
  assert.equal(chat.currentSessionId.value, null)
})

test('chat input creates separate assistant message and releases busy state', async () => {
  const realStore = await store()
  const chat = new Proxy(realStore, {get: (target, key) => key === 'currentSessionId' ? target[key].value : target[key]})
  const source = await readFile(new URL('../src/components/ChatInput.vue', import.meta.url), 'utf8')
  const script = source.match(/<script setup>([\s\S]*?)<\/script>/)[1].replace(/^import .*$/gm, '')
  class Stream {
    constructor() { this.handlers = {} }
    on(event, handler) { this.handlers[event] = handler; return this }
    async start() { this.handlers.token('answer'); this.handlers.source({source_name:'doc',source_detail:'1'}); this.handlers.close() }
  }
  const input = vm.runInNewContext(script + '\n;({sendMessage, inputText, isStreaming})', {
    ref: value => ({value}), onBeforeUnmount() {}, useChatStore: () => chat,
    useAuthStore: () => ({token: 'token'}), useRouter: () => ({}), SSEClient: Stream,
  })
  input.inputText.value = 'question'
  await input.sendMessage()
  const messages = chat.currentSession().messages
  assert.equal(messages.length, 2)
  assert.equal(messages[0].role, 'user')
  assert.equal(messages[0].content, 'question')
  assert.equal(messages[1].role, 'assistant')
  assert.equal(messages[1].content, 'answer')
  assert.equal(messages[1].sources.length, 1)
  assert.equal(input.isStreaming.value, false)
})

