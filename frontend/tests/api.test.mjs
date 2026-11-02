import test from 'node:test'
import assert from 'node:assert/strict'
import { api, setToken, hasToken, loadSession } from '../src/lib/api.js'

// 模拟冲突响应，验证弹框需要的关联对象不会被通用请求层丢弃。
test('role deletion errors retain structured references for the dialog',async()=>{
  const original=globalThis.fetch
  const references=[{type:'member',id:'employee',name:'张三',rules:[]}]
  globalThis.fetch=async()=>new Response(JSON.stringify({detail:'角色仍有关联',details:{kind:'role_in_use',references}}),{status:409})
  try{
    await assert.rejects(()=>api('/roles/test',{method:'DELETE'}),error=>{
      assert.equal(error.message,'角色仍有关联');assert.equal(error.status,409)
      assert.deepEqual(error.details.references,references);return true
    })
  }finally{globalThis.fetch=original}
})

test('session token survives a page reload and logout removes it',async()=>{
  const originalStorage=Object.getOwnPropertyDescriptor(globalThis,'sessionStorage')
  const originalFetch=globalThis.fetch
  const values=new Map()
  Object.defineProperty(globalThis,'sessionStorage',{configurable:true,value:{
    getItem:key=>values.get(key)??null,setItem:(key,value)=>values.set(key,value),removeItem:key=>values.delete(key)
  }})
  let authorization
  globalThis.fetch=async(url,options)=>{authorization=options.headers.Authorization;return new Response('{}')}
  try {
    const first=await import('../src/lib/api.js?session-first')
    first.setToken('v3s_test-session')
    assert.equal(values.size,1)
    const reloaded=await import('../src/lib/api.js?session-reloaded')
    await reloaded.api('/me')
    assert.equal(authorization,'Bearer v3s_test-session')
    reloaded.setToken('')
    assert.equal(values.size,0)
    const loggedOut=await import('../src/lib/api.js?session-logged-out')
    await loggedOut.api('/me')
    assert.equal(authorization,'Bearer ')
  } finally {
    globalThis.fetch=originalFetch
    if(originalStorage)Object.defineProperty(globalThis,'sessionStorage',originalStorage)
    else delete globalThis.sessionStorage
  }
})

test('restoring a session reloads current permissions and only requests allowed resources',async()=>{
  const original=globalThis.fetch
  const paths=[]
  const me={id:'employee',permissions:['knowledge.read']}
  globalThis.fetch=async url=>{
    paths.push(url)
    return new Response(JSON.stringify(url.endsWith('/me')?me:[]))
  }
  try {
    setToken('v3s_current')
    const session=await loadSession()
    assert.deepEqual(session.me,me)
    assert.deepEqual(session.backends,[])
    assert.deepEqual(paths.sort(),['/api/v3/conversations','/api/v3/me','/api/v3/spaces'])
    paths.length=0;me.permissions.push('system.manage')
    await loadSession()
    assert.ok(paths.includes('/api/v3/backends'))
  }finally{setToken('');globalThis.fetch=original}
})

test('expired or disabled sessions are cleared, temporary service failures retain the token',async()=>{
  const original=globalThis.fetch
  try {
    for(const status of [401,403,503]){
      setToken('v3s_current')
      globalThis.fetch=async()=>new Response(JSON.stringify({detail:'unavailable'}),{status})
      await assert.rejects(()=>loadSession(),error=>error.status===status)
      assert.equal(hasToken(),status===503)
    }
  }finally{setToken('');globalThis.fetch=original}
})

test('blocked browser storage still allows an in-memory login',async()=>{
  const descriptor=Object.getOwnPropertyDescriptor(globalThis,'sessionStorage')
  Object.defineProperty(globalThis,'sessionStorage',{configurable:true,get(){throw new Error('storage disabled')}})
  try {
    const isolated=await import('../src/lib/api.js?blocked-storage')
    isolated.setToken('v3s_current')
    assert.equal(isolated.hasToken(),true)
    isolated.setToken('')
    assert.equal(isolated.hasToken(),false)
  }finally{
    if(descriptor)Object.defineProperty(globalThis,'sessionStorage',descriptor)
    else delete globalThis.sessionStorage
  }
})
