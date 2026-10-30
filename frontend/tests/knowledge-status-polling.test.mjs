import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'

function page(api) {
  const source = readFileSync(new URL('../src/views/KnowledgeBase.vue', import.meta.url), 'utf8')
    .split('<script setup>')[1].split('</script>')[0].replace(/^import .*$/gm, '')
  let mount, unmount, tick, cleared = false
  const context = vm.createContext({
    ref: value => ({ value }), knowledgeApi: { list: async () => [], ...api },
    onMounted: fn => { mount = fn }, onUnmounted: fn => { unmount = fn },
    setInterval: fn => { tick = fn; return 1 }, clearInterval: () => { cleared = true },
  })
  vm.runInContext(source + '\nthis.page = { selectKb, docs, selected };', context)
  return { ...context.page, mount: () => mount(), tick: () => tick?.(), unmount: () => unmount?.(), cleared: () => cleared }
}

test('processing changes to failed automatically, retaining failure detail', async () => {
  let calls = 0
  const view = page({ listDocs: async () => ++calls === 1
    ? [{ id: 'doc', status: 'processing' }]
    : [{ id: 'doc', status: 'failed', error_msg: 'Embedding connection timeout' }] })
  await view.mount()
  await view.selectKb({ id: 'kb' })
  await view.tick()
  assert.equal(view.docs.value[0].status, 'failed')
  assert.match(view.docs.value[0].error_msg, /timeout/)
  await view.tick()
  assert.equal(calls, 2, 'terminal documents should not be polled')
  view.unmount()
  assert.equal(view.cleared(), true)
})

test('a slow previous knowledge base response cannot replace the selected list', async () => {
  let finishA
  const view = page({ listDocs: id => id === 'a'
    ? new Promise(resolve => { finishA = resolve })
    : Promise.resolve([{ id: 'b-doc', status: 'done' }]) })
  const pendingA = view.selectKb({ id: 'a' })
  await view.selectKb({ id: 'b' })
  finishA([{ id: 'a-doc', status: 'processing' }])
  await pendingA
  assert.equal(view.docs.value[0].id, 'b-doc')
})

test('a temporary polling failure can be retried without overlapping requests', async () => {
  let calls = 0, finish
  const view = page({ listDocs: async () => {
    calls++
    if (calls === 1) return [{ id: 'doc', status: 'processing' }]
    if (calls === 2) throw new Error('offline')
    return new Promise(resolve => { finish = resolve })
  } })
  await view.mount()
  await view.selectKb({ id: 'kb' })
  await view.tick()
  const pending = view.tick()
  await view.tick()
  assert.equal(calls, 3)
  finish([{ id: 'doc', status: 'done', chunk_count: 48 }])
  await pending
  assert.equal(view.docs.value[0].chunk_count, 48)
})
