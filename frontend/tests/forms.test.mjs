import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import vm from 'node:vm'

const errors = (await readFile(new URL('../src/utils/errors.js', import.meta.url), 'utf8')).replace('export function', 'function')
async function view(name, exports, bindings) {
  const text = await readFile(new URL(`../src/views/${name}.vue`, import.meta.url), 'utf8')
  return vm.runInNewContext(errors + text.match(/<script setup>([\s\S]*?)<\/script>/)[1].replace(/^import .*$/gm, '') + `\n;({${exports}})`, {
    ref: value => ({value}), onMounted() {}, ...bindings,
  })
}

test('admin rejected submission keeps values, displays server validation and permits retry', async () => {
  let attempts = 0
  const adminApi = {
    async createUser() { attempts++; if (attempts === 1) throw {response:{data:{detail:[{msg:'duplicate user'}]}}} },
    async getUsers() { return [] }, async getDBConnections() { return [] }, async getLLMConfigs() { return [] },
  }
  const page = await view('Admin','createUser,userForm,error,busy,showUserForm', {adminApi})
  page.userForm.value = {username:'alice',password:'secret',role:'user'}
  page.showUserForm.value = true
  await page.createUser()
  assert.equal(page.error.value,'duplicate user')
  assert.equal(page.busy.value,false)
  assert.equal(page.userForm.value.username,'alice')
  assert.equal(page.showUserForm.value,true)
  await page.createUser()
  assert.equal(attempts,2)
  assert.equal(page.error.value,'')
  assert.equal(page.showUserForm.value,false)
})

test('document upload refreshes counts and prevents concurrent submissions', async () => {
  let finish
  let uploads = 0
  const page = await view('KnowledgeBase','uploadDoc,selectedKb,file,kbs,busy', {
    useAuthStore: () => ({}),
    uploadDocument: () => { uploads++; return new Promise(resolve => {finish=resolve}) },
    listKnowledgeBases: async () => [{id:'kb',doc_count:1}],
  })
  page.selectedKb.value = {id:'kb'}
  page.file.value = {name:'doc.txt'}
  const pending = page.uploadDoc()
  await page.uploadDoc()
  assert.equal(uploads,1)
  assert.equal(page.busy.value,true)
  finish()
  await pending
  assert.equal(page.kbs.value[0].doc_count,1)
  assert.equal(page.selectedKb.value,null)
  assert.equal(page.busy.value,false)
})

test('login 401 stays on form while authenticated API 401 expires session', async () => {
  const source = await readFile(new URL('../src/api/http.js', import.meta.url), 'utf8')
  let rejectHandler
  let logouts = 0
  const location = {href:'/'}
  vm.runInNewContext(source.replace(/^import .*$/gm,'').replace('export default http',''), {
    axios: {create: () => ({interceptors:{request:{use(){}},response:{use: (_,reject) => {rejectHandler=reject}}}})},
    useAuthStore: () => ({logout() {logouts++}}), window:{location},
  })
  await assert.rejects(rejectHandler({response:{status:401},config:{url:'/v1/auth/login'}}))
  assert.equal(logouts,0)
  await assert.rejects(rejectHandler({response:{status:401},config:{url:'/v1/admin/users'}}))
  assert.equal(logouts,1)
  assert.equal(location.href,'/login')
})
