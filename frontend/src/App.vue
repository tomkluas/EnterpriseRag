<script setup>
import { computed,ref,onMounted,onUnmounted,watch,nextTick } from 'vue'
import MarkdownIt from 'markdown-it'
import DOMPurify from 'dompurify'
import AppDialog from './components/AppDialog.vue'
import DocumentsWorkspace from './components/DocumentsWorkspace.vue'
import OrganizationAdmin from './components/OrganizationAdmin.vue'
import TraceAdmin from './components/TraceAdmin.vue'
import JobsWorkspace from './components/JobsWorkspace.vue'
import AuditWorkspace from './components/AuditWorkspace.vue'
import { api,setToken,hasToken,loadSession,answer,download } from './lib/api'
import { navigationFor } from './lib/navigation.mjs'

// 根组件统筹身份、导航和问答，资料及组织管理交给独立工作区。
const me=ref(null),access=ref(''),page=ref('chat'),busy=ref(false),error=ref(''),notice=ref('')
const restoring=ref(hasToken())
const spaces=ref([]),selected=ref(''),documents=ref([]),backends=ref([]),jobs=ref([]),users=ref([])
const conversations=ref([]),conversation=ref(''),messages=ref([]),question=ref(''),status=ref(''),streaming=ref(false),source=ref(null)
const chatScroll=ref(null),followLatest=ref(true)
function trackChatScroll(){const el=chatScroll.value;if(el)followLatest.value=el.scrollHeight-el.scrollTop-el.clientHeight<64}
function scrollToLatest(){const el=chatScroll.value;if(el)el.scrollTop=el.scrollHeight}
async function showLatest(){followLatest.value=true;await nextTick();scrollToLatest()}
// 只在用户停留在底部时跟随流式输出，向上阅读历史时保持位置。
watch(()=>[messages.value.length,messages.value.at(-1)?.answer,status.value],async()=>{
  if(followLatest.value){await nextTick();scrollToLatest()}
},{flush:'post'})
const switchTarget=ref(''),switchInfo=ref(null)
const backendForm=ref({provider:'weknora',base_url:'',key_ref:'V3_SECRET_WK_READ',write_key_ref:'V3_SECRET_WK_WRITE',manage_key_ref:'V3_SECRET_WK_MANAGE',collection_config:{collection_id:''}})
const loginMode=ref('password'),loginId=ref(''),password=ref(''),dialog=ref(null)
const feedbackText=ref(''),trace=ref(null)
const copiedAnswer=ref('')
const stageNames={request:'整体处理',rewrite:'问题改写',retrieve:'检索资料',rerank:'证据精排',generate:'生成回答'}
const outcomeNames={ok:'完成',error:'失败',cancelled:'已中止'}
// 分别保存回答流与任务轮询句柄，退出或卸载时统一清理。
let controller=null,poller=null
// 禁止 Markdown 原生 HTML，并在写入 v-html 前再次清理生成标记。
const md=new MarkdownIt({html:false,linkify:true,breaks:true})
const render=value=>DOMPurify.sanitize(md.render(value||''))
const activeSpace=computed(()=>spaces.value.find(s=>s.id===selected.value))
const activeBackend=computed(()=>backends.value.find(b=>b.id===activeSpace.value?.active_backend))
const has=permission=>me.value?.permissions?.includes(permission)
// 按当前身份展示入口，服务端仍校验每次请求的实际操作权限。
const nav=computed(()=>navigationFor(me.value?.permissions))
// 身份刷新后若当前入口已被收回，退出工作区并停止其轮询。
watch(nav,items=>{if(!items.some(item=>item.id===page.value))void openPage('chat')})
const label={received:'已接收',queued:'等待处理',running:'处理中',backend_processing:'建立索引',ready:'可发布',published:'已发布',failed:'失败',completed:'已完成',needs_reconciliation:'待人工对账',delete_requested:'等待清理确认',cancelled:'已取消'}

// 统一收敛操作的忙碌状态和错误，成功提示由具体动作设置。
async function run(action){error.value='';notice.value='';busy.value=true;try{return await action()}catch(e){error.value=e.message}finally{busy.value=false}}
async function refreshSpaces(){spaces.value=await api('/spaces');if(!spaces.value.some(s=>s.id===selected.value))selected.value=spaces.value[0]?.id||''}
async function enterWorkspace(){
  const session=await loadSession()
  spaces.value=session.spaces;selected.value=spaces.value[0]?.id||''
  conversations.value=session.conversations;backends.value=session.backends
  me.value=session.me;page.value='chat'
}
onMounted(async()=>{
  if(!restoring.value)return
  try{await run(enterWorkspace)}finally{restoring.value=false}
})
// 身份验证与初始数据全部加载成功后，才进入已登录界面。
async function login(){await run(async()=>{
  try{
    // 先用候选令牌加载身份和初始数据，全部成功后再设置 me，避免进入半初始化工作台。
    const token=loginMode.value==='password'?(await api('/auth/login',{method:'POST',body:{id:loginId.value.trim(),password:password.value}})).access_token:access.value.trim()
    setToken(token);await enterWorkspace()
    access.value='';password.value=''
  }catch(e){setToken('');throw e}
})}
// 注销失败时也清除本页身份与业务快照，并停止后台活动。
async function logout(){controller?.abort();clearInterval(poller);try{await api('/auth/logout',{method:'POST'})}catch{}finally{
  setToken('');me.value=null;messages.value=[];source.value=null;dialog.value=null;conversation.value='';conversations.value=[]
  spaces.value=[];documents.value=[];backends.value=[];users.value=[];jobs.value=[];selected.value='';notice.value='';error.value=''
}}
// 离开任务页停止轮询，轮询时跳过正在执行其他操作的时刻。
async function openPage(id){if(!nav.value.some(item=>item.id===id))id='chat';page.value=id;source.value=null;clearInterval(poller);await run(async()=>{
  if(id==='backends')backends.value=await api('/backends')
  if(id==='jobs'){jobs.value=await api('/jobs');poller=setInterval(()=>{if(!busy.value)api('/jobs').then(x=>jobs.value=x).catch(()=>clearInterval(poller))},5000)}
})}
async function selectSpace(){switchInfo.value=null;if(page.value!=='documents')await openPage(page.value)}
function chooseSpace(id){selected.value=id;switchInfo.value=null}
// 自身账号或所属角色改变后重新登录，避免继续使用旧权限快照。
async function identityChanged(){await logout();notice.value='账号或角色已更新，请重新登录以载入最新权限。'}
async function receiveNotice(value){notice.value=value;try{me.value=await api('/me')}catch{}}
function openDialog(kind,item=null,opener=document.activeElement){error.value='';dialog.value={kind,item,opener}}
async function register(){await run(async()=>{await api('/backends',{method:'POST',body:backendForm.value});backends.value=await api('/backends');dialog.value=null;notice.value='底座已登记，完成实际验证并导入签收报告后才能启用'})}
// 先准备目标索引，再携带已读取的路由版本激活，由服务端校验版本。
async function prepare(){await run(async()=>{if(!selected.value||!switchTarget.value)throw new Error('请选择知识库和目标底座');const task=await api(`/spaces/${selected.value}/backend-switches`,{method:'POST',body:{target_instance_id:switchTarget.value}});switchInfo.value=await api(`/spaces/${selected.value}/backend-switches/${task.id}`)})}
async function inspect(){await run(async()=>{switchInfo.value=await api(`/spaces/${selected.value}/backend-switches/${switchInfo.value.id}`)})}
async function activate(){await run(async()=>{await api(`/spaces/${selected.value}/backend-switches/${switchInfo.value.id}/activate`,{method:'POST',body:{expected_route_revision:switchInfo.value.route_revision}});await refreshSpaces();notice.value='当前知识库已切换到目标底座';switchInfo.value=null})}
async function newConversation(){if(streaming.value)return;followLatest.value=true;conversation.value=(await api('/conversations',{method:'POST'})).id;messages.value=[];conversations.value=await api('/conversations')}
async function loadConversation(id){if(streaming.value)return;conversation.value=id;messages.value=await api(`/conversations/${id}/messages`);await showLatest()}
// 一次只发送一个问题，先插入待完成消息，再按事件累积证据与答案。
async function send(retryQuery,retrySpace){const text=typeof retryQuery==='string'?retryQuery:question.value;if(!text.trim()||streaming.value)return;await run(async()=>{
  // 会话在首个问题前创建；本地占位消息随后用服务端返回的真实消息 ID 替换。
  const spaceId=retrySpace||selected.value
  if(!spaceId)throw new Error('请先创建或选择知识库')
  if(!spaces.value.some(s=>s.id===spaceId))throw new Error('原知识库已不可用，请重新选择知识库后提问')
  if(!conversation.value)await newConversation()
  selected.value=spaceId
  followLatest.value=true;const query=text.trim();if(typeof retryQuery!=='string')question.value='';messages.value.push({id:'pending',query,space_id:spaceId,answer:'',evidence:[],state:'processing'});const item=messages.value[messages.value.length-1]
  controller=new AbortController();streaming.value=true;status.value='正在准备'
  // 仅 done 确认完成，主动停止与其他异常都会保留未完成标记。
  try{await answer(conversation.value,{space_id:selected.value,query},controller.signal,event=>{
    // 状态、来源和正文分别到达；正文只追加增量，收到 done 才能把消息视为已完成。
    if(event.message_id)item.id=event.message_id
    if(event.event==='status')status.value=event.data
    if(event.event==='sources')item.evidence=event.data
    if(event.event==='token')item.answer+=event.data
    if(event.event==='done'){item.id=event.data.message_id;item.state='completed';status.value=''}
    if(event.event==='error'){item.state='failed';throw new Error(event.data)}
  })}catch(e){item.state='failed';if(e.name!=='AbortError')throw e}finally{
    // 停止或失败保留已有正文，结束后都释放发送状态；主动中止不额外显示错误提示。
    streaming.value=false;status.value='';controller=null
    // 首问已由服务端保存后刷新标题，回答失败或中止也保留该话题。
    if(me.value){try{conversations.value=await api('/conversations')}catch{}}
  }
})}
async function removeConversation(){await run(async()=>{
  if(streaming.value)throw new Error('请先停止回答再删除会话')
  const id=dialog.value.item.id
  await api(`/conversations/${encodeURIComponent(id)}`,{method:'DELETE'})
  conversations.value=await api('/conversations')
  if(conversation.value===id){conversation.value='';messages.value=[];source.value=null}
  dialog.value=null;notice.value='会话已从个人列表删除，审计记录保留。'
})}
function feedback(item){feedbackText.value='';openDialog('feedback',item)}
async function copyAnswer(item){if(!item.answer||item.state==='restricted')return;await run(async()=>{
  try{await navigator.clipboard.writeText(item.answer)}catch{
    const field=document.createElement('textarea');field.value=item.answer;field.readOnly=true
    field.style.position='fixed';field.style.left='-10000px';document.body.append(field)
    const focus=document.activeElement
    try{field.select();if(!document.execCommand('copy'))throw new Error('复制失败，请手动选择答案复制')}finally{field.remove();focus?.focus()}
  }
  copiedAnswer.value=item.id
})}
async function saveFeedback(){await run(async()=>{if(!feedbackText.value.trim())throw new Error('请填写反馈内容');await api(`/messages/${dialog.value.item.id}/feedback`,{method:'POST',body:{text:feedbackText.value.trim()}});dialog.value=null;notice.value='反馈已保存'})}
async function showTrace(item,event){const opener=event?.currentTarget;await run(async()=>{trace.value=await api(`/messages/${item.id}/trace`);openDialog('trace',item,opener)})}
async function submitDialog(){if(dialog.value?.kind==='feedback')await saveFeedback();if(dialog.value?.kind==='backend')await register();if(dialog.value?.kind==='deleteConversation')await removeConversation()}
async function getSource(e){await run(()=>download(e.document_id,e.version_id,documents.value.find(d=>d.id===e.document_id)?.name||'原始资料'))}
async function retry(job){await run(async()=>{await api(`/jobs/${job.id}/retry`,{method:'POST'});jobs.value=await api('/jobs')})}
onUnmounted(()=>{controller?.abort();clearInterval(poller)})
</script>

<template>
  <!-- 登录页与工作台互斥，两者之间保持紧邻的条件分支。 -->
  <main v-if="!me" class="login-shell">
    <section class="login-intro"><div class="brand-mark">知</div><p class="wordmark">EnterpriseRag <span>V3</span></p><h1>让企业资料<br>成为可靠的答案。</h1><p>从授权的知识中查找依据，保留每一次回答的来源。</p><div class="intro-rule"></div><p class="intro-note">企业知识工作台</p></section>
    <section class="login-panel"><p v-if="restoring" role="status">正在恢复登录…</p><form v-else class="login-form" @submit.prevent="login">
      <span class="small-label">欢迎回来</span><h2>进入知识工作台</h2>
      <div class="segmented"><button type="button" :class="{chosen:loginMode==='password'}" @click="loginMode='password'">账号密码</button><button type="button" :class="{chosen:loginMode==='token'}" @click="loginMode='token'">企业令牌</button></div>
      <template v-if="loginMode==='password'"><p>使用管理员为你创建的账号和密码登录。</p><label>账号<input v-model="loginId" autocomplete="username" required placeholder="输入成员账号"></label><label>密码<input v-model="password" type="password" autocomplete="current-password" required placeholder="输入登录密码"></label></template>
      <template v-else><p>使用企业身份系统签发的访问令牌，或开发环境启动令牌。</p><label>访问令牌<input v-model="access" type="password" name="access_token" autocomplete="current-password" spellcheck="false" required placeholder="粘贴访问令牌"></label></template>
      <p v-if="error" class="error" role="alert">{{ error }}</p><p v-if="notice" class="success" role="status">{{ notice }}</p><button class="primary" :disabled="busy">{{ busy?'正在验证…':'登录工作台' }}</button><small>刷新页面会恢复当前标签页的登录状态；关闭标签页或登录过期后需重新登录。忘记密码请联系管理员重置。</small>
    </form></section>
  </main>
  <div v-else class="workspace" :class="{'chat-workspace':page==='chat'}">
    <!-- 左栏提供导航和会话，主区域按页面切换业务内容。 -->
    <aside class="sidebar"><div class="brand"><div class="brand-mark">知</div><div>EnterpriseRag<small>企业知识工作台</small></div></div><nav><button v-for="item in nav" :key="item.id" :class="{active:page===item.id}" @click="openPage(item.id)"><span>{{ item.symbol }}</span>{{ item.title }}</button></nav><div v-if="page==='chat'" class="conversation-list"><button @click="run(newConversation)">＋ 新建会话</button><div v-for="c in conversations" :key="c.id" class="conversation-row" :class="{chosen:c.id===conversation}"><button class="conversation-open" :title="c.title" :disabled="streaming" @click="run(()=>loadConversation(c.id))">{{ c.title==='新会话'?'知识问答 '+c.id.slice(0,5):c.title }}</button><button class="conversation-delete" :aria-label="'删除会话：'+c.title" title="删除会话" :disabled="busy||streaming" @click="openDialog('deleteConversation',c)">×</button></div></div><div class="account"><span class="avatar">{{ me.id.slice(0,1) }}</span><div>{{ me.id }}<small>{{ me.role_names?.join('、')||'未分配角色' }}</small></div><button title="退出登录" @click="logout">↪</button></div></aside>
    <div class="main-pane"><header><div><span class="small-label">{{ me.tenant_name||'企业' }}</span><select v-model="selected" @change="selectSpace"><option value="" disabled>选择知识库</option><option v-for="s in spaces" :value="s.id" :key="s.id">{{ s.name }}</option></select></div><span class="version-tag">V3 <span>知识工作台</span></span></header>
      <div v-if="error" class="banner error" role="alert">{{ error }}<button @click="error=''">×</button></div><div v-if="notice" class="banner success" role="status">{{ notice }}<button @click="notice=''">×</button></div>
      <section v-if="page==='chat'" class="chat-page"><div ref="chatScroll" class="chat-scroll" @scroll.passive="trackChatScroll"><div v-if="!messages.length" class="chat-empty"><div class="knowledge-symbol">⌕</div><span class="small-label">从资料到答案</span><h1>{{ spaces.length?'今天想了解什么？':'还没有可使用的知识库' }}</h1><p v-if="spaces.length">选择知识库，提出一个具体问题。<br>回答会附上可核查的资料片段。</p><p v-else>{{ has('knowledge.create')?'请先到知识资料页面创建知识库，上传并发布资料。':'请联系资料管理员，为你的角色授权并发布资料。完成后点击下方按钮刷新。' }}</p><button v-if="!spaces.length" class="secondary" :disabled="busy" @click="run(refreshSpaces)">刷新可用知识库</button><div v-if="spaces.length" class="suggestions"><button @click="question='请介绍这份资料的主要规定，并列出依据。'">了解主要规定 <span>↗</span></button><button @click="question='这项制度适用于哪些情况？有哪些例外？'">确认适用范围 <span>↗</span></button></div></div>
        <div v-else class="messages"><article v-for="(item,index) in messages" :key="index" class="message"><div class="question">{{ item.query }}</div><div class="answer-heading"><span class="answer-dot"></span>知识回答<span v-if="item.state==='failed'" class="muted">未完成</span></div><div class="answer-content" v-html="render(item.answer)"></div><p v-if="item.state==='processing'&&!item.answer" class="muted">{{ status||'正在处理…' }}</p><div v-if="item.evidence?.length" class="sources"><button v-for="(e,i) in item.evidence" :key="e.chunk_id" @click="source=e"><span>{{ i+1 }}</span>资料依据 {{ i+1 }}<small>{{ e.locator_level==='page'?'第 '+e.locator.page+' 页':'证据片段' }}</small></button></div><div class="message-actions"><button v-if="item.answer&&item.state!=='processing'&&item.state!=='restricted'" class="text-button copy-answer" :disabled="busy" aria-label="复制答案" @click="copyAnswer(item)">{{ copiedAnswer===item.id?'已复制':'复制答案' }}</button><button v-if="item.state==='failed'" class="text-button" :disabled="busy||streaming" :title="item.space_id?'在原知识库重新回答，保留原记录':'使用当前所选知识库重新回答，保留原记录'" @click="send(item.query,item.space_id)">重试回答</button><button v-if="item.state==='completed'" class="text-button" @click="feedback(item)">反馈回答</button><button v-if="item.id!=='pending'&&item.state!=='processing'" class="text-button" :disabled="busy" @click="showTrace(item,$event)">处理明细 / Trace</button></div></article></div>
        </div><div class="chat-scroll-controls"><button v-if="messages.length&&!followLatest" type="button" class="text-button" @click="showLatest">↓ 回到最新消息</button></div>
        <form class="composer" @submit.prevent="send"><textarea v-model="question" rows="2" :disabled="streaming" placeholder="输入你的问题，例如：差旅报销需要哪些材料？" @keydown.enter.exact.prevent="send"></textarea><div><span>{{ streaming?status:'仅检索你有权访问的资料' }}</span><button v-if="streaming" type="button" class="secondary" @click="controller?.abort()">停止回答</button><button v-else class="primary" :disabled="!question.trim()||!selected">发送 ↑</button></div></form><small class="chat-footnote">重要业务决策请核对原文。资料不足时，系统会明确说明。</small></section>
      <DocumentsWorkspace v-if="page==='documents'" :spaces="spaces" :selected="selected" :me="me" @select-space="chooseSpace" @refresh-spaces="run(refreshSpaces)" @notice="notice=$event" />
      <section v-if="page==='backends'" class="content-page backend-page">
        <div class="page-heading"><div><h1>知识底座</h1><p>底座负责解析和检索资料。每个知识库选择一个当前使用的服务。</p></div><button class="primary" @click="openDialog('backend')">登记新服务</button></div>
        <section class="backend-current"><div><span class="small-label">当前知识库</span><h2>{{ activeSpace?.name||'请先选择知识库' }}</h2><p>{{ activeBackend?'资料检索由 '+(activeBackend.provider==='dify'?'Dify':'WeKnora')+' 提供服务':'尚未配置服务，登记并验证后可在下方准备切换。' }}</p></div><div v-if="activeBackend" class="current-service"><span class="badge ready">当前使用</span><strong>{{ activeBackend.provider==='dify'?'Dify':'WeKnora' }}</strong><small>{{ activeBackend.base_url }}</small><small>实例 {{ activeBackend.id.slice(0,10) }}</small></div></section>
        <section class="backend-switch-panel"><h2>切换知识库使用的服务</h2><p>先同步资料并检查索引，全部就绪后再切换。准备期间，问答继续使用当前服务。</p>
          <div class="switch-steps"><div><strong>1. 选择目标服务</strong><span>仅可选择已通过当前环境验证的服务。</span></div><div><strong>2. 同步资料</strong><span>准备索引后，可重新检查处理进度。</span></div><div><strong>3. 确认切换</strong><span>索引和权限检查通过后启用目标服务。</span></div></div>
          <div class="toolbar"><label>目标服务<select v-model="switchTarget" @change="switchInfo=null"><option value="" disabled>选择服务</option><option v-for="b in backends.filter(b=>b.id!==activeSpace?.active_backend)" :key="b.id" :value="b.id" :disabled="!b.usable">{{ b.provider==='dify'?'Dify':'WeKnora' }} · {{ b.id.slice(0,8) }}{{ b.usable?'':'（待验证）' }}</option></select></label><button class="secondary" :disabled="busy||!selected||!switchTarget" @click="prepare">准备并同步资料</button></div>
          <div v-if="switchInfo" class="switch-status"><div><strong>目标索引 {{ switchInfo.ready }} / {{ switchInfo.total }} 已就绪</strong><small>{{ switchInfo.production_verified?'生产验收通过':switchInfo.verified?'本机文档试运行':'服务尚未验证' }}</small></div><progress :value="switchInfo.ready" :max="switchInfo.total||1" :aria-label="'索引就绪 '+switchInfo.ready+' / '+switchInfo.total"></progress><button class="secondary" :disabled="busy" @click="inspect">重新检查</button><button class="primary" :disabled="busy||!switchInfo.verified||switchInfo.ready!==switchInfo.total" @click="activate">确认切换</button></div>
        </section>
        <section class="service-catalog"><div class="section-heading"><div><h2>已登记的服务</h2><p>登记仅保存连接配置；验证通过后才可用于问答。</p></div><button class="secondary" :disabled="busy" @click="openPage('backends')">刷新列表</button></div>
          <div class="table-scroll"><table><thead><tr><th>服务 / 实例</th><th>连接地址</th><th>验证状态</th><th>当前知识库</th></tr></thead><tbody><tr v-for="b in backends" :key="b.id"><td><strong>{{ b.provider==='dify'?'Dify':'WeKnora' }}</strong><small>{{ b.id.slice(0,10) }}</small></td><td class="service-url">{{ b.base_url }}</td><td><span class="badge" :class="{ready:b.usable}">{{ b.verified?'生产验收通过':b.usable?'本机文档试运行':'等待验证' }}</span></td><td>{{ b.id===activeSpace?.active_backend?'正在使用':'—' }}</td></tr></tbody></table></div>
          <div v-if="!backends.length" class="empty-row">还没有登记服务。点击“登记新服务”开始配置。</div><details class="help-details"><summary>待验证的服务如何启用？</summary><p>请部署管理员完成服务连接、文档导入和检索验证，再通过管理命令导入验收报告。这里只展示验证结果，登记后不会直接启用。</p></details>
        </section>
      </section>
      <JobsWorkspace v-if="page==='jobs'" :jobs="jobs" :busy="busy" @refresh="openPage('jobs')" @retry="retry" />
      <OrganizationAdmin v-if="page==='directory'" :me="me" @identity-changed="identityChanged" @notice="receiveNotice" />
      <TraceAdmin v-if="page==='traces'" />
      <AuditWorkspace v-if="page==='audit'" />
    </div><aside v-if="source" class="source-drawer"><button class="drawer-close" @click="source=null">×</button><span class="small-label">回答依据</span><h2>证据片段</h2><p class="muted">{{ source.locator_level==='page'?'第 '+source.locator.page+' 页':'原文页码未确认，以下为实际检索片段' }}</p><blockquote>{{ source.content }}</blockquote><small>版本 {{ source.version_id.slice(0,12) }}</small><button class="primary" @click="getSource(source)">下载原文</button></aside>
  </div>
  <!-- 反馈、处理明细及底座登记复用弹框，并保留各自打开时的焦点。 -->
  <AppDialog v-if="dialog" :title="({feedback:'反馈回答',trace:'问答处理明细',backend:'登记知识服务',deleteConversation:'删除会话'})[dialog.kind]" :description="({feedback:'告诉我们哪里需要改进，反馈会保存到本次回答。',trace:'查看本次问答各处理阶段的耗时与结果。',backend:'连接配置由部署管理员提供，凭据填写环境变量名称。',deleteConversation:dialog.item?.title})[dialog.kind]" :busy="busy" :return-focus="dialog.opener" :readonly="dialog.kind==='trace'" :danger="dialog.kind==='deleteConversation'" :submit-label="({feedback:'保存反馈',backend:'登记服务',deleteConversation:'确认删除'})[dialog.kind]" @close="dialog=null" @submit="submitDialog">
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-if="dialog.kind==='deleteConversation'">删除后，这个会话将从你的列表移除，不能继续提问。审计记录仍保留。确认删除吗？</p>
    <label v-if="dialog.kind==='feedback'">反馈内容<textarea v-model="feedbackText" rows="5" required maxlength="2000" placeholder="例如：引用的制度已经过期，或回答遗漏了适用条件。"></textarea><small>{{ feedbackText.length }} / 2000</small></label>
    <template v-if="dialog.kind==='trace'"><div class="trace-summary"><span class="badge" :class="{ready:trace?.state==='completed'}">{{ trace?.state==='completed'?'回答完成':trace?.state==='processing'?'正在处理':'回答未完成' }}</span><small>消息 {{ trace?.message_id }}</small></div>
      <table v-if="trace?.stages?.length"><thead><tr><th>处理阶段</th><th>耗时</th><th>结果</th></tr></thead><tbody><tr v-for="(step,index) in trace.stages" :key="index"><td>{{ stageNames[step.stage]||step.stage }}</td><td>{{ step.milliseconds }} ms</td><td>{{ outcomeNames[step.outcome]||step.outcome }}</td></tr></tbody></table>
      <p v-else class="empty-row">本次回答暂无处理明细。历史回答不会补生成 Trace；刚结束的回答可关闭后重新查看。</p><p v-if="trace?.failure" class="error">{{ trace.failure.detail }}<small>{{ trace.failure.code }}</small></p><p class="permission-note">仅展示实际执行的阶段。没有可用证据时不调用生成模型；整体耗时包含流式传输与必要的权限检查。</p>
    </template>
    <template v-if="dialog.kind==='backend'"><div class="dialog-fields"><label>服务平台<select v-model="backendForm.provider"><option value="weknora">WeKnora</option><option value="dify">Dify</option></select></label><label>服务地址<input v-model="backendForm.base_url" type="url" required placeholder="https://knowledge.company.example"></label><label>平台知识库 ID<input v-model="backendForm.collection_config.collection_id" required placeholder="平台中已创建的知识库 ID"></label><div class="credential-fields"><h3>服务凭据</h3><p>填写部署环境中的变量名称，不要填写密钥本身。</p><label>检索凭据引用<input v-model="backendForm.key_ref" required pattern="V3_SECRET_.+"></label><label>写入凭据引用<input v-model="backendForm.write_key_ref" pattern="V3_SECRET_.+"></label><label>管理凭据引用<input v-model="backendForm.manage_key_ref" pattern="V3_SECRET_.+"></label></div></div></template>
  </AppDialog>
</template>
