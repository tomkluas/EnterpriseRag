import { readEvents } from './stream.mjs'

// 标签页会话存储允许刷新后恢复登录；不存密码、角色或业务数据。
const tokenKey='enterpriserag.v3.session-token'
let token=''
try{token=globalThis.sessionStorage?.getItem(tokenKey)||''}catch{}
export function hasToken(){return Boolean(token)}
export function setToken(value){
  token=value
  // 浏览器禁止存储时仍可使用当前页面内存登录。
  try{if(value)globalThis.sessionStorage?.setItem(tokenKey,value);else globalThis.sessionStorage?.removeItem(tokenKey)}catch{}
}
export async function api(path,options={}){
  const headers={Authorization:'Bearer '+token,...options.headers}
  // FormData 由浏览器生成 multipart 边界，其他请求体统一编码为 JSON。
  if(options.body && !(options.body instanceof FormData)){
    headers['Content-Type']='application/json';options={...options,body:JSON.stringify(options.body)}
  }
  // 调用方取消信号优先，未指定时使用普通请求的默认超时。
  const response=await fetch('/api/v3'+path,{...options,headers,signal:options.signal||AbortSignal.timeout(30000)})
  // 保留状态码和结构化关联信息，供角色删除等弹框说明具体阻碍。
  if(!response.ok){const data=await response.json().catch(()=>({}));const error=new Error(typeof data.detail==='string'?data.detail:`操作失败（${response.status}）`);error.details=data.details;error.status=response.status;throw error}
  if(response.status===204)return
  return response.json()
}
// 登录和刷新复用初始化流程；身份与权限每次从后端读取，不信任浏览器缓存。
export async function loadSession(){
  try{
    const me=await api('/me')
    const [spaces,conversations,backends]=await Promise.all([
      api('/spaces'),api('/conversations'),me.permissions.includes('system.manage')?api('/backends'):Promise.resolve([])
    ])
    return {me,spaces,conversations,backends}
  }catch(error){
    // 暂时断网或服务不可用不等于登录失效，保留令牌供刷新重试。
    if(error.status===401||error.status===403)setToken('')
    throw error
  }
}
// 回答流由页面控制中止，逐个转交解析后的事件以更新消息。
export async function answer(conversation,body,signal,onEvent){
  // 流请求沿用页面提供的取消信号，事件协议负责判断完成，不能只看 HTTP 请求是否成功。
  const response=await fetch(`/api/v3/conversations/${conversation}/messages/stream`,{
    method:'POST',headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'},body:JSON.stringify(body),signal})
  if(!response.ok){const e=await response.json().catch(()=>({}));throw new Error(typeof e.detail==='string'?e.detail:'提问未完成')}
  for await(const event of readEvents(response.body))onEvent(event)
}
// 受保护原件通过带身份的请求下载，不能直接使用普通链接。
export async function download(document,version,name){
  // 文档与版本同时传给服务端复查归属；下载旧版本也必须满足当前权限。
  const url=`/api/v3/documents/${encodeURIComponent(document)}/source?version_id=${encodeURIComponent(version)}`
  const response=await fetch(url,{headers:{Authorization:'Bearer '+token}})
  if(!response.ok)throw new Error('无法下载原文，资料可能已撤回或权限已变化')
  // 延迟回收 Blob 地址，留出浏览器接管下载的时间。
  const object=URL.createObjectURL(await response.blob());const a=documentThis().createElement('a');a.href=object;a.download=name||'原文';a.click();setTimeout(()=>URL.revokeObjectURL(object),1000)
}
function documentThis(){return window.document}
