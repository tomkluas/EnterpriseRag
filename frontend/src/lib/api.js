import { readEvents } from './stream.mjs'

let token=''
export function setToken(value){token=value}
export async function api(path,options={}){
  const headers={Authorization:'Bearer '+token,...options.headers}
  if(options.body && !(options.body instanceof FormData)){
    headers['Content-Type']='application/json';options={...options,body:JSON.stringify(options.body)}
  }
  const response=await fetch('/api/v3'+path,{...options,headers,signal:options.signal||AbortSignal.timeout(30000)})
  if(!response.ok){const error=await response.json().catch(()=>({}));throw new Error(typeof error.detail==='string'?error.detail:`操作失败（${response.status}）`)}
  if(response.status===204)return
  return response.json()
}
export async function answer(conversation,body,signal,onEvent){
  const response=await fetch(`/api/v3/conversations/${conversation}/messages/stream`,{
    method:'POST',headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'},body:JSON.stringify(body),signal})
  if(!response.ok){const e=await response.json().catch(()=>({}));throw new Error(typeof e.detail==='string'?e.detail:'提问未完成')}
  for await(const event of readEvents(response.body))onEvent(event)
}
export async function download(document,version,name){
  const url=`/api/v3/documents/${encodeURIComponent(document)}/source?version_id=${encodeURIComponent(version)}`
  const response=await fetch(url,{headers:{Authorization:'Bearer '+token}})
  if(!response.ok)throw new Error('无法下载原文，资料可能已撤回或权限已变化')
  const object=URL.createObjectURL(await response.blob());const a=documentThis().createElement('a');a.href=object;a.download=name||'原文';a.click();setTimeout(()=>URL.revokeObjectURL(object),1000)
}
function documentThis(){return window.document}
