import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'

function store() {
  let id = 0
  const source = readFileSync(new URL('../src/stores/chat.js', import.meta.url), 'utf8')
    .replace(/^import .*$/gm, '').replace('export const useChatStore', 'const useChatStore')
  const ctx = vm.createContext({ defineStore: (_, factory) => factory, ref: value => ({ value }), uuidv4: () => String(++id) })
  vm.runInContext(source + '\nthis.store = useChatStore()', ctx)
  return ctx.store
}

test('stream tokens and sources reach their original session after switching', () => {
  const chat = store()
  const first = chat.newSession()
  const msgId = chat.addMessage({ role: 'assistant' })
  chat.newSession()
  chat.appendToken(msgId, 'hello')
  chat.addSource(msgId, { source_name: 'file', source_detail: 'page' })
  const message = chat.sessions.value.find(s => s.id === first).messages[0]
  assert.equal(message.content, 'hello')
  assert.equal(message.sources.length, 1)
})

function chatPage() {
  const clients = []
  class Client {
    constructor() { this.handlers = {}; clients.push(this) }
    on(event, handler) { this.handlers[event] = handler; return this }
    start() { return new Promise(resolve => { this.finish = resolve }) }
    abort() {}
  }
  const source = readFileSync(new URL('../src/views/Chat.vue', import.meta.url), 'utf8')
    .split('<script setup>')[1].split('</script>')[0].replace(/^import .*$/gm, '')
  const ctx = vm.createContext({
    ref: value => ({ value }), computed: fn => ({ get value() { return fn() } }),
    watch() {}, onMounted() {}, onUnmounted() {}, nextTick: fn => fn(), SSEClient: Client,
    CHAT_STREAM_URL: '/', useChatStore: () => ({ currentSessionId: 's', currentSession: () => null, addMessage: () => 'm', appendToken() {}, addSource() {} }),
    useAuthStore: () => ({ token: 'fake' }), useConfigStore: () => ({}),
  })
  vm.runInContext(source + '\nthis.page = {handleSend, handleStop, isStreaming}', ctx)
  return { ...ctx.page, clients }
}

test('completion of a stopped request cannot clear a newer request loading state', async () => {
  const page = chatPage()
  const first = page.handleSend({ query: 'one' })
  page.handleStop()
  const second = page.handleSend({ query: 'two' })
  page.clients[0].handlers.done()
  page.clients[0].finish()
  await first
  assert.equal(page.isStreaming.value, true)
  page.clients[1].handlers.done()
  page.clients[1].finish()
  await second
  assert.equal(page.isStreaming.value, false)
})

test('input permits a plain-model question with all retrieval scopes disabled', () => {
  const source = readFileSync(new URL('../src/components/ChatInput.vue', import.meta.url), 'utf8')
    .split('<script setup>')[1].split('</script>')[0].replace(/^import .*$/gm, '')
  let payload
  const ctx = vm.createContext({
    ref: value => ({ value }), onMounted() {}, defineProps: () => ({ isStreaming: false }),
    defineEmits: () => (_, data) => { payload = data }, useAuthStore: () => ({}),
  })
  vm.runInContext(source + '\ntext.value="hello"; scopeVector.value=false; handleSend()', ctx)
  assert.equal(payload.scope_vector, false)
})
