<script setup>
import { computed, onUnmounted, ref, watch, nextTick } from 'vue'
import AppDialog from './AppDialog.vue'
import { api, download } from '../lib/api'
import './documents-workspace.css'

// 父组件提供知识库和身份，本组件维护资料列表、筛选与操作弹框。
const props = defineProps({ spaces: { type: Array, default: () => [] }, selected: { type: String, default: '' }, me: { type: Object, default: () => ({}) } })
const emit = defineEmits(['select-space', 'refresh-spaces', 'notice'])
const documents = ref([]), loading = ref(false), busy = ref(false), error = ref(''), search = ref(''), filter = ref('all'), detailId = ref('')
const dialog = ref(null), dialogError = ref(''), selectedFile = ref(null), spaceName = ref(''), roleOptions = ref([]), roleSearch = ref('')
const grantsLoaded = ref(false)
const grantForm = ref({ role_ids: [], denied_role_ids: [], inherit: true })
const activeSpace = computed(() => props.spaces.find(s => s.id === props.selected))
const canCreate = computed(() => props.me.permissions?.includes('knowledge.create'))
const detail = computed(() => documents.value.find(d => d.id === detailId.value))
const visibleDocuments = computed(() => documents.value.filter(d => {
  const match = d.name.toLocaleLowerCase().includes(search.value.trim().toLocaleLowerCase())
  return match && (filter.value === 'all' || (filter.value === 'published' ? !!d.current_version : filter.value === 'unpublished' ? !d.current_version : filter.value === 'failed' ? latest(d)?.state === 'failed' : ['ready'].includes(latest(d)?.state)))
}))
const visibleRoles = computed(() => roleOptions.value.filter(r => r.label.toLocaleLowerCase().includes(roleSearch.value.trim().toLocaleLowerCase())))
const states = { received: '已接收', queued: '等待处理', running: '处理中', backend_processing: '建立索引', ready: '可发布', published: '已发布', failed: '失败', completed: '已完成', needs_reconciliation: '待人工对账', delete_requested: '等待清理确认', cancelled: '已取消' }
const dialogTitle = computed(() => ({ create: '新建知识库', upload: '上传资料', version: '上传新版本', grant: '资料角色授权', spaceGrant: '知识库默认角色授权', withdraw: '撤回资料', cancel: '取消失败草稿', publish: '发布版本' })[dialog.value?.kind])
const dialogDescription = computed(() => ['create'].includes(dialog.value?.kind) ? '创建后可在“默认角色授权”中调整阅读规则。' : dialog.value?.item?.name || activeSpace.value?.name || '')
// 每次加载递增代次并取消旧请求，防止已返回但未应用的旧响应覆盖新列表。
let generation = 0, controller = null, disposed = false
const pathId = id => encodeURIComponent(id)
// 管理员可查看处理中的版本，阅读者只展示当前或曾发布的版本。
function versions(doc) { return (doc?.versions || []).filter(v => doc.can_manage || v.id === doc.current_version || v.state === 'published') }
function latest(doc) { return [...versions(doc)].sort((a, b) => Number(b.created_at) - Number(a.created_at))[0] }
function publishable(doc) { const version=latest(doc);return doc.can_manage&&!doc.withdrawn&&version?.state==='ready'&&doc.current_version!==version.id }
async function showVersions(doc) { detailId.value=doc.id;await nextTick();document.getElementById('dw-detail')?.scrollIntoView({block:'start'}) }
function date(value) { return value ? new Date(typeof value === 'number' ? value * 1000 : value).toLocaleString('zh-CN') : '时间未知' }
function roleIds(values) { return (values || []).filter(x => typeof x === 'string' && x.startsWith('role:')).map(x => x.slice(5)) }

// 应用响应前同时核对组件生命周期、请求代次和当前知识库。
async function load(spaceId = props.selected) {
  // 取消只能阻止尚未完成的请求，代次校验还要拦住已经返回、正在等待应用的旧结果。
  controller?.abort(); const request = ++generation
  if (!spaceId) { documents.value = []; loading.value = false; return }
  controller = new AbortController(); loading.value = true; error.value = ''
  try {
    const data = await api(`/spaces/${pathId(spaceId)}/documents`, { signal: controller.signal })
    if (disposed || request !== generation || spaceId !== props.selected) return
    documents.value = data
    // 当前详情可能因撤回或权限变更消失，刷新后清掉失效选择，不继续展示旧对象。
    if (!data.some(d => d.id === detailId.value)) detailId.value = ''
  } catch (e) { if (!disposed && request === generation && e.name !== 'AbortError') error.value = e.message }
  finally { if (!disposed && request === generation) loading.value = false }
}
// 切库时清空详情与弹框，避免把旧知识库的操作带入新上下文。
watch(() => props.selected, () => {
  documents.value = []; detailId.value = ''; selectedFile.value = null; dialog.value = null
  search.value = ''; filter.value = 'all'; dialogError.value = ''; load()
}, { immediate: true })
onUnmounted(() => { disposed = true; generation++; controller?.abort() })

function open(kind, item = null, version = null) {
  if (busy.value) return
  // 把触发时的知识库固定在弹框里，提交时据此检查是否已经切库。
  dialogError.value = ''; selectedFile.value = null; spaceName.value = ''; roleSearch.value = ''
  dialog.value = { kind, item, version, spaceId: props.selected, opener: document.activeElement }
}
function close() { if (!busy.value) { dialog.value = null; selectedFile.value = null; dialogError.value = '' } }
// 角色选项必须成功加载后才能提交，避免用空列表覆盖现有规则。
async function grants(kind, item) {
  open(kind, item); const current = dialog.value; if (!current) return
  roleOptions.value = []; grantsLoaded.value = false; busy.value = true
  grantForm.value = { role_ids: roleIds(item.readers), denied_role_ids: roleIds(item.denied), inherit: item.inherit_permissions ?? true }
  // 将 role: 标识拆成纯角色 ID；加载失败时保持不可提交，避免误把授权清空。
  try {
    const target = kind === 'spaceGrant' ? `spaces/${pathId(item.id)}` : `documents/${pathId(item.id)}`
    const options = await api(`/${target}/grant-options`)
    if (dialog.value !== current || disposed) return
    roleOptions.value = options.filter(o => o.kind === 'role')
    // 保留已配置但停用或消失的角色，避免保存时悄然删除旧规则。
    for (const id of [...grantForm.value.role_ids, ...grantForm.value.denied_role_ids]) {
      if (!roleOptions.value.some(r => r.id === id)) roleOptions.value.push({ id, label: id, kind: 'role', active: false, description: '角色已停用或不在当前目录中' })
    }
    grantsLoaded.value = true
  } catch (e) { if (dialog.value === current) dialogError.value = e.message }
  finally { busy.value = false }
}
// 固定本次弹框与知识库快照，异步结果只更新仍匹配的界面。
async function submit() {
  const current = dialog.value
  // 异步期间始终使用本次弹框快照，后续关闭/切库不应让响应修改另一个操作的状态。
  if (!current || busy.value) return
  busy.value = true; dialogError.value = ''
  try {
    const { kind, item, version, spaceId } = current
    let message = ''
    if (kind === 'create') {
      if (!canCreate.value || !spaceName.value.trim()) throw new Error('请填写知识库名称')
      const result = await api('/spaces', { method: 'POST', body: { name: spaceName.value.trim() } })
      emit('refresh-spaces'); emit('select-space', result.id); message = '知识库已创建，可继续设置默认角色授权。'
    } else if (kind === 'upload' || kind === 'version') {
      if (!activeSpace.value?.can_manage || spaceId !== props.selected || (item && !item.can_manage)) throw new Error('当前没有上传权限，请刷新后重试')
      if (!selectedFile.value) throw new Error('请先选择文件')
      if (selectedFile.value.size > 50 * 1024 * 1024) throw new Error('文件大小不能超过 50 MiB')
      // 每次上传附带独立幂等键，供服务端识别同一次提交。
      const form = new FormData()
      form.append('space_id', spaceId); form.append('document_id', item?.id || '')
      form.append('idempotency_key', crypto.randomUUID()); form.append('file', selectedFile.value)
      await api('/documents', { method: 'POST', body: form }); message = '文件已接收，处理完成后可在版本详情中发布。'
    } else if (kind === 'grant' || kind === 'spaceGrant') {
      // 只有文档支持继承开关；知识库自身保存默认规则，不发送无意义的 inherit。
      if (!item.can_manage) throw new Error('当前没有修改授权的权限')
      if (!grantsLoaded.value) throw new Error('角色列表未能加载，请关闭弹框后重新打开，避免覆盖现有授权')
      const body = { role_ids: [...grantForm.value.role_ids], denied_role_ids: [...grantForm.value.denied_role_ids] }
      if (kind === 'grant') body.inherit = grantForm.value.inherit
      await api(`/${kind === 'spaceGrant' ? 'spaces' : 'documents'}/${pathId(item.id)}/grants`, { method: 'POST', body })
      if (kind === 'spaceGrant') emit('refresh-spaces')
      message = '角色授权已更新，明确禁止优先于允许。'
    } else {
      if (!item.can_manage) throw new Error('当前没有维护这份资料的权限')
      // 版本动作携带文档和版本 ID，撤回针对整个文档，具体状态条件由服务端复查。
      if (kind === 'withdraw') await api(`/documents/${pathId(item.id)}`, { method: 'DELETE' })
      else await api(`/documents/${pathId(item.id)}/versions/${pathId(version.id)}/${kind}`, { method: 'POST' })
      message = kind === 'withdraw' ? '资料已撤回。' : kind === 'publish' ? '版本已发布。' : '失败草稿已取消，当前已发布版本继续生效。'
    }
    // 退出后不再通知或刷新，切库后也不加载旧知识库的列表。
    if (disposed) return
    if (dialog.value === current) { dialog.value = null; selectedFile.value = null }
    emit('notice', message)
    if (spaceId === props.selected && kind !== 'create') await load(spaceId)
  } catch (e) { if (dialog.value === current) dialogError.value = e.message }
  finally { busy.value = false }
}
async function getSource(doc, version) {
  if (busy.value) return
  busy.value = true; error.value = ''
  try { await download(doc.id, version.id, doc.name) } catch (e) { error.value = e.message } finally { busy.value = false }
}
</script>

<template>
  <section class="documents-workspace" aria-label="知识资料工作台">
    <div class="dw-heading"><div><h1>知识资料</h1><p>在知识库中查找资料，查看已发布内容与版本。</p></div><button class="secondary" :disabled="loading || busy" @click="load(); emit('refresh-spaces')">刷新状态</button></div>
    <!-- 左侧选择知识库，右侧从列表进入版本详情；管理操作按对象权限展示。 -->
    <div class="dw-layout">
      <aside class="dw-spaces" aria-label="知识库列表">
        <div class="dw-section-title"><h2>知识库</h2><span>{{ spaces.length }}</span></div>
        <button v-if="canCreate" class="secondary dw-create" :disabled="busy" @click="open('create')">＋ 新建知识库</button>
        <div class="dw-space-list"><button v-for="space in spaces" :key="space.id" :class="['dw-space', { 'dw-selected': selected === space.id }]" :aria-pressed="selected === space.id" :disabled="busy" @click="emit('select-space', space.id)"><span class="dw-space-symbol" aria-hidden="true">▤</span><span>{{ space.name }}<small>{{ space.can_manage ? '可管理' : '可阅读' }}</small></span></button></div>
        <p v-if="!spaces.length" class="dw-hint">暂无可用知识库。{{ canCreate ? '点击上方按钮创建。' : '请联系管理员分配阅读权限。' }}</p>
      </aside>
      <div class="dw-main">
        <template v-if="activeSpace">
          <div class="dw-summary"><div><h2>{{ activeSpace.name }}</h2><p>{{ documents.length }} 份可访问资料<span v-if="!activeSpace.can_manage"> · 仅展示有权阅读的已发布内容</span></p></div><div class="dw-actions"><button v-if="activeSpace.can_manage" class="secondary" :disabled="busy" @click="grants('spaceGrant', activeSpace)">默认角色授权</button><button v-if="activeSpace.can_manage" class="primary" :disabled="busy" @click="open('upload')">上传资料</button></div></div>
          <div class="dw-filters"><label>搜索资料<input v-model="search" type="search" placeholder="输入文件名"></label><label>发布与处理状态<select v-model="filter"><option value="all">全部状态</option><option value="published">已有发布版本</option><option value="unpublished">尚未发布</option><option value="ready">最新版本可发布</option><option value="failed">最新版本失败</option></select></label><span class="dw-result" aria-live="polite">{{ visibleDocuments.length }} 份资料</span></div>
          <p v-if="error" class="error" role="alert">{{ error }} <button class="text-button" :disabled="loading || busy" @click="load()">重试</button></p>
          <p v-if="loading" class="dw-empty" role="status">正在读取资料…</p>
          <template v-else>
            <div v-if="visibleDocuments.length" class="dw-table-wrap"><table class="dw-table"><caption class="dw-visually-hidden">{{ activeSpace.name }}的资料</caption><thead><tr><th scope="col">文件名</th><th scope="col">发布情况</th><th scope="col">最新状态</th><th scope="col">版本数</th><th scope="col">操作</th></tr></thead><tbody><tr v-for="doc in visibleDocuments" :key="doc.id" :class="{ 'dw-current-row': detailId === doc.id }"><td><button class="dw-document-name" :aria-expanded="detailId === doc.id" aria-controls="dw-detail" @click="showVersions(doc)">{{ doc.name }}</button></td><td><span class="badge" :class="doc.current_version ? 'published' : ''">{{ doc.current_version ? '已发布' : '未发布' }}</span></td><td><span class="badge" :class="latest(doc)?.state">{{ states[latest(doc)?.state] || latest(doc)?.state || '暂无版本' }}</span></td><td>{{ versions(doc).length }}</td><td><div class="dw-row-actions"><button v-if="publishable(doc)" class="text-button dw-publish" :disabled="busy" @click="open('publish', doc, latest(doc))">发布</button><button class="text-button" @click="showVersions(doc)">查看版本</button><button v-if="doc.can_manage" class="text-button" :disabled="busy" @click="grants('grant', doc)">角色授权</button><button v-if="doc.can_manage" class="text-button danger" :disabled="busy" @click="open('withdraw', doc)">撤回</button></div></td></tr></tbody></table></div>
            <div v-else-if="!error" class="dw-empty"><h3>{{ documents.length ? '没有符合条件的资料' : '还没有可访问的资料' }}</h3><p>{{ documents.length ? '试着修改文件名或选择其他状态。' : activeSpace.can_manage ? '上传资料，检查处理结果后发布。' : '资料发布并授权给你的角色后，会显示在这里。' }}</p></div>
          </template>
          <section v-if="detail" id="dw-detail" class="dw-detail" aria-label="资料版本详情"><div class="dw-detail-heading"><div><span class="small-label">版本详情</span><h2>{{ detail.name }}</h2></div><div class="dw-actions"><button v-if="detail.can_manage && activeSpace.can_manage" class="secondary" :disabled="busy" @click="open('version', detail)">上传新版本</button><button class="text-button" aria-label="关闭版本详情" @click="detailId = ''">关闭</button></div></div><p v-if="detail.can_manage" class="dw-hint">上传与处理不会替换现有发布版本；确认内容后再发布。</p><ol class="dw-versions"><li v-for="version in [...versions(detail)].sort((a, b) => Number(b.created_at) - Number(a.created_at))" :key="version.id"><div><strong>{{ date(version.created_at) }}</strong><small>版本 {{ version.id.slice(0, 12) }}</small></div><span class="badge" :class="version.state">{{ states[version.state] || version.state }}</span><span v-if="detail.current_version === version.id" class="dw-live">当前生效</span><div class="dw-version-actions"><button v-if="detail.can_manage && version.state === 'ready' && detail.current_version !== version.id" class="text-button" :disabled="busy" @click="open('publish', detail, version)">发布此版本</button><button v-if="detail.can_manage && version.can_cancel" class="text-button danger" :disabled="busy" @click="open('cancel', detail, version)">取消失败草稿</button><button v-if="detail.can_read" class="text-button" :disabled="busy" @click="getSource(detail, version)">下载原件</button></div></li></ol><p v-if="!versions(detail).length" class="dw-hint">暂无可查看的版本。</p></section>
        </template>
        <div v-else class="dw-empty"><h2>选择一个知识库</h2><p>从左侧选择知识库，查看其中的资料。</p></div>
      </div>
    </div>
    <!-- 变更先在弹框确认，上传、处理与发布保持为分开的步骤。 -->
    <AppDialog v-if="dialog" :key="dialog.kind + (dialog.item?.id || '')" :title="dialogTitle" :description="dialogDescription" :busy="busy" :return-focus="dialog.opener" :danger="dialog.kind === 'withdraw'" :submit-label="({create:'创建知识库', upload:'上传资料', version:'上传新版本', grant:'保存角色授权', spaceGrant:'保存默认授权', withdraw:'确认撤回', cancel:'确认取消草稿', publish:'发布此版本'})[dialog.kind]" @close="close" @submit="submit">
      <p v-if="dialogError" class="error" role="alert">{{ dialogError }}</p>
      <label v-if="dialog.kind === 'create'">知识库名称<input v-model="spaceName" required maxlength="128" placeholder="例如：售后服务制度" :disabled="busy"><small>默认采用你当前所属角色作为阅读范围。</small></label>
      <template v-if="dialog.kind === 'upload' || dialog.kind === 'version'"><p v-if="dialog.kind === 'version'">为“{{ dialog.item.name }}”添加新版本。原发布版本继续生效。</p><label>选择文件<input type="file" required :disabled="busy" :accept="activeSpace?.allowed_formats?.join(',') || '.pdf,.docx,.xlsx,.txt,.md'" @change="selectedFile = $event.target.files?.[0] || null"><small>支持 {{ activeSpace?.allowed_formats?.join('、') || 'PDF、Word、Excel、TXT、Markdown' }}，单文件最大 50 MiB。</small></label><p v-if="selectedFile" class="dw-hint">{{ selectedFile.name }}（{{ (selectedFile.size / 1024 / 1024).toFixed(2) }} MiB）</p></template>
      <template v-if="dialog.kind === 'grant' || dialog.kind === 'spaceGrant'">
        <p class="dw-grant-note">阅读权限按角色分配。一个成员属于多个角色时，任何角色的明确禁止都会优先于允许。停用角色不授予阅读权限。</p>
        <label v-if="dialog.kind === 'grant'" class="dw-inherit"><input v-model="grantForm.inherit" type="checkbox" :disabled="busy">继承知识库默认允许角色</label>
        <p v-if="dialog.kind === 'grant'" class="dw-hint">{{ grantForm.inherit ? '在默认允许角色上追加下方允许角色。' : '仅采用本资料的允许角色。' }}知识库和资料中的明确禁止均生效。</p>
        <p v-if="dialog.kind === 'grant' && grantForm.inherit && grantsLoaded" class="dw-hint">知识库默认允许：{{ roleIds(activeSpace?.readers).map(id=>roleOptions.find(role=>role.id===id)?.label||'未知角色').join('、')||'未设置' }}。</p>
        <label>搜索角色<input v-model="roleSearch" type="search" placeholder="输入角色名称" :disabled="busy"></label>
        <div class="dw-grant-table"><table><thead><tr><th scope="col">角色</th><th scope="col">允许阅读</th><th scope="col">明确禁止</th></tr></thead><tbody><tr v-for="role in visibleRoles" :key="role.id"><td>{{ role.label }}<small>{{ role.active ? role.description || '启用中' : '已停用或不可用' }}</small></td><td><input v-model="grantForm.role_ids" type="checkbox" :value="role.id" :disabled="busy || (!role.active && !grantForm.role_ids.includes(role.id))" :aria-label="'允许角色 ' + role.label + ' 阅读'"></td><td><input v-model="grantForm.denied_role_ids" type="checkbox" :value="role.id" :disabled="busy" :aria-label="'明确禁止角色 ' + role.label + ' 阅读'"></td></tr></tbody></table><p v-if="!visibleRoles.length" class="dw-hint">{{ busy ? '正在读取角色…' : '没有匹配的角色。' }}</p></div><small>已允许 {{ grantForm.role_ids.length }} 个角色，明确禁止 {{ grantForm.denied_role_ids.length }} 个角色。</small>
      </template>
      <template v-if="dialog.kind === 'withdraw'"><p>撤回“{{ dialog.item.name }}”后，这份资料将立即停止用于问答与下载，后台会继续清理索引。</p><p>确认不再需要使用这份资料后，再执行撤回。</p></template>
      <p v-if="dialog.kind === 'cancel'">仅取消此失败草稿。当前已发布版本继续生效；切换知识底座前需要重新准备目标索引。</p>
      <p v-if="dialog.kind === 'publish'">将 {{ date(dialog.version.created_at) }} 上传的版本设为当前生效版本。之后的问答将使用这个版本。</p>
    </AppDialog>
  </section>
</template>
