<script setup>
import { computed, onMounted, ref } from 'vue'
import AppDialog from './AppDialog.vue'
import { api } from '../lib/api'
import './organization-admin.css'

// 企业、部门、角色与成员共用页面，系统操作权限与资料阅读授权分别维护。
const props = defineProps({ me: { type: Object, required: true } })
const emit = defineEmits(['identity-changed', 'notice'])
const allowed = permission => (props.me.permissions || []).includes(permission)
// 按身份过滤标签和数据请求，提交时再次检查对应操作权限。
const tabs = computed(() => [
  { id: 'tenant', name: '企业信息', permission: 'system.manage' },
  { id: 'organization', name: '组织架构', permission: 'organization.manage' },
  { id: 'roles', name: '角色', permission: 'roles.manage' },
  { id: 'members', name: '成员', permission: 'members.manage' },
].filter(item => allowed(item.permission)))
const tab = ref(tabs.value[0]?.id || '')
const tenant = ref(null), units = ref([]), roles = ref([]), members = ref([]), catalog = ref([])
const loading = ref(false), loadError = ref(''), dialog = ref(null), form = ref({}), formError = ref(''), saving = ref(false)
const roleReferences=ref([]),referenceLoading=ref(false),referencesLoaded=ref(false)
const search = ref(''), selectedUnit = ref(''), companyName = ref(''), companyBusy = ref(false), companyError = ref('')
const permissionNames = computed(() => new Map(catalog.value.map(item => [item.id, item.label])))
const unitNames = computed(() => new Map(units.value.map(item => [item.id, item.name])))
// 访问集合防止异常环造成无限递归，根树之外的节点仍以顶层展示。
const tree = computed(() => {
  const result = [], seen = new Set()
  function visit(parent, depth) {
    for (const item of units.value.filter(item => (item.parent_id || '') === parent)) {
      if (seen.has(item.id)) continue
      // 访问过的节点不再递归；异常成环的数据也不能导致整个组织页面卡死。
      seen.add(item.id); result.push({ ...item, depth }); visit(item.id, depth + 1)
    }
  }
  visit('', 0)
  // 父节点缺失或孤立成环的记录仍展示，方便管理员发现数据异常。
  for (const item of units.value) if (!seen.has(item.id)) result.push({ ...item, depth: 0 })
  return result
})
const filteredTree = computed(() => tree.value.filter(item => item.name.toLowerCase().includes(search.value.trim().toLowerCase())))
// 部门筛选包含全部下级，逐轮扩展集合直到没有新增节点。
const selectedUnitIds = computed(() => {
  const ids = new Set(selectedUnit.value ? [selectedUnit.value] : [])
  let changed = true
  while (changed) {
    // 每轮只加入新节点，有限记录最多加入一次，层级多深都能覆盖并最终停止。
    changed = false
    for (const item of units.value) if (ids.has(item.parent_id) && !ids.has(item.id)) { ids.add(item.id); changed = true }
  }
  return ids
})
const visibleMembers = computed(() => members.value.filter(item => {
  const term = search.value.trim().toLowerCase()
  return (!selectedUnit.value || selectedUnitIds.value.has(item.org_unit_id)) &&
    [item.id, item.display_name, ...(item.role_names || [])].some(value => String(value || '').toLowerCase().includes(term))
}))
const visibleRoles = computed(() => roles.value.filter(item => `${item.name} ${item.description || ''}`.toLowerCase().includes(search.value.trim().toLowerCase())))
// 编辑部门时排除自身与全部下级，避免选择成环的上级。
const parentOptions = computed(() => {
  if (!form.value.id || dialog.value?.kind !== 'organization') return tree.value
  const excluded = new Set([form.value.id]); let changed = true
  // 沿父链扩展到所有后代，不能只排除直属下级，否则仍可能把祖先移到孙级节点下成环。
  while (changed) { changed = false; for (const item of units.value) if (excluded.has(item.parent_id) && !excluded.has(item.id)) { excluded.add(item.id); changed = true } }
  return tree.value.filter(item => !excluded.has(item.id))
})
// 停用的既有分配仍可显示和移除，新增分配仅允许启用对象。
const selectableRoles = computed(() => roles.value.filter(item => item.active || form.value.role_ids?.includes(item.id)))
const selectableUnits = computed(() => tree.value.filter(item => item.active || item.id === form.value.org_unit_id))
function memberCount(id) { return members.value.filter(item => item.org_unit_id === id).length }
function switchTab(id) { tab.value = id; search.value = '' }
// 目录请求并行读取，部分失败仍保留成功项，并汇总失败原因。
async function load() {
  if (loading.value) return
  loading.value = true; loadError.value = ''
  const jobs = []
  function read(path, target) { jobs.push(api(path).then(value => { target.value = value })) }
  if (allowed('system.manage')) read('/tenant', tenant)
  if (allowed('organization.manage') || allowed('members.manage')) read('/organization', units)
  if (allowed('roles.manage') || allowed('members.manage')) read('/roles', roles)
  if (allowed('roles.manage')) read('/permission-catalog', catalog)
  if (allowed('members.manage')) read('/users', members)
  const results = await Promise.allSettled(jobs)
  // 某一目录失败不丢弃其他成功结果，逐项汇总错误，等待所有请求结束再释放加载状态。
  loadError.value = results.filter(item => item.status === 'rejected').map(item => item.reason.message).join('；')
  if (tenant.value) companyName.value = tenant.value.name
  if (selectedUnit.value && !units.value.some(item => item.id === selectedUnit.value)) selectedUnit.value = ''
  loading.value = false
}
// 克隆数组隔离未保存编辑，密码重新输入，触发按钮用于恢复焦点。
function open(kind, item, event, remove = false) {
  if (saving.value || loading.value) return
  const defaults = kind === 'organization' ? { name: '', parent_id: units.value.find(unit => !unit.parent_id && unit.active)?.id || '', active: true } : kind === 'roles' ? { name: '', description: '', permissions: [], active: true } : { id: '', display_name: '', org_unit_id: units.value.find(item => !item.parent_id && item.active)?.id || '', role_ids: [], active: true, password: '' }
  form.value = { ...defaults, ...item, permissions: [...(item?.permissions || [])], role_ids: [...(item?.role_ids || [])], password: '' }
  // 数组单独复制，勾选不会直接修改列表；密码始终清空，编辑不能回显或复用旧输入。
  formError.value = '';roleReferences.value=[];referenceLoading.value=false;referencesLoaded.value=false; dialog.value = { kind, editing: Boolean(item), remove, original: item, returnFocus: event?.currentTarget }
  if(kind==='roles'&&remove)loadRoleReferences(dialog.value)
}
// 引用查询只写回发起它的弹框，旧响应不能覆盖后来打开的角色。
async function loadRoleReferences(current){
  referenceLoading.value=true
  // 返回时用发起查询的弹框对象匹配，避免旧角色查询覆盖后来打开的新弹框。
  try{
    const result=await api(`/roles/${encodeURIComponent(current.original.id)}/references`)
    if(dialog.value===current){roleReferences.value=result.references;referencesLoaded.value=true}
  }catch(error){if(dialog.value===current)formError.value=error.message}
  finally{if(dialog.value===current)referenceLoading.value=false}
}
function inspectUsage(role,event){open('roles',role,event);if(dialog.value){dialog.value.usage=true;loadRoleReferences(dialog.value)}}
function close() { if (!saving.value) { form.value.password = ''; dialog.value = null } }
const dialogTitle = computed(() => dialog.value?.usage?`角色使用情况：${dialog.value.original.name}`:`${dialog.value?.remove ? '删除' : dialog.value?.editing ? '编辑' : '新增'}${{ organization: '部门', roles: '角色', members: '成员' }[dialog.value?.kind] || ''}`)
async function saveCompany() {
  if (companyBusy.value || !allowed('system.manage')) return
  companyError.value = ''
  if (!companyName.value.trim()) { companyError.value = '请输入企业名称。'; return }
  companyBusy.value = true
  try { await api('/tenant', { method: 'PUT', body: { name: companyName.value.trim() } }); tenant.value.name = companyName.value.trim(); const root=units.value.find(unit=>!unit.parent_id);if(root)root.name=companyName.value.trim();emit('notice', '企业名称已保存。') }
  catch (error) { companyError.value = error.message }
  finally { companyBusy.value = false }
}
// 删除角色先确认关联查询已完成，冲突时仍以服务端返回的引用为准。
async function submit() {
  if (saving.value || !dialog.value) return
  const current = dialog.value, value = form.value, completedTitle = dialogTitle.value
  if(current.usage||referenceLoading.value)return
  if(current.kind==='roles'&&current.remove&&!referencesLoaded.value){formError.value='未能确认角色关联，请关闭后重试。';return}
  // 前端查询只用于说明，服务端删除时仍在事务内重查，覆盖查询后新增引用的并发情况。
  const permission = { organization: 'organization.manage', roles: 'roles.manage', members: 'members.manage' }[current.kind]
  if (!allowed(permission)) { formError.value = '你已没有此操作的权限，请重新登录。'; return }
  formError.value = ''
  roleReferences.value=[]
  if (!current.remove) {
    if (current.kind === 'members') {
      if (!value.id.trim() || !value.display_name.trim()) { formError.value = '请填写成员账号和姓名。'; return }
      if (!value.org_unit_id) { formError.value = '请选择所属部门。'; return }
      if (!current.editing && !value.password) { formError.value = '新增成员必须设置初始密码。'; return }
      if (value.password && value.password.length < 12) { formError.value = '密码至少需要 12 位。'; return }
    } else if (!value.name.trim()) { formError.value = '请输入名称。'; return }
  }
  saving.value = true
  try {
    const path = { organization: '/organization', roles: '/roles', members: '/users' }[current.kind]
    let identityChanged = false
    if (current.remove) await api(`${path}/${encodeURIComponent(value.id)}`, { method: 'DELETE' })
    else {
      let body
      if (current.kind === 'organization') body = { name: value.name.trim(), parent_id: value.parent_id || '', active: value.active }
      if (current.kind === 'roles') body = { name: value.name.trim(), description: value.description.trim(), permissions: [...value.permissions], active: value.active }
      if (current.kind === 'members') {
        // 编辑留空表示保持原密码，因此空值不能放入请求体；新增成员则已要求初始密码。
        body = { id: value.id.trim(), display_name: value.display_name.trim(), org_unit_id: value.org_unit_id, role_ids: [...value.role_ids], active: value.active }
        if (value.password) body.password = value.password
        const before = current.original
        // 修改自身认证信息或角色分配后重新登录，以重新取得权限快照。
        identityChanged = current.editing && value.id === props.me.id && (Boolean(value.password) || before.active !== value.active || JSON.stringify([...before.role_ids].sort()) !== JSON.stringify([...value.role_ids].sort()))
      }
      if (current.editing) body.id = value.id
      await api(path, { method: 'POST', body })
      if (current.kind === 'roles' && current.editing && (props.me.role_ids || []).includes(value.id)) identityChanged = true
    }
    form.value.password = ''; dialog.value = null
    // 自身身份变化立即重新登录，不再使用旧权限快照发起后续目录刷新。
    if (identityChanged) { emit('identity-changed'); return }
    emit('notice', `${completedTitle}已完成。`)
    await load()
  } catch (error) { formError.value = error.message;roleReferences.value=Array.isArray(error.details?.references)?error.details.references:[] }
  finally { saving.value = false }
}
onMounted(load)
</script>

<template>
  <section class="content-page organization-admin">
    <div class="page-heading"><div><h1>企业与成员</h1><p>维护企业组织、角色与成员。部门上下级关系不会自动授予资料访问权。</p></div><button class="secondary" :disabled="loading || saving || companyBusy" @click="load">{{ loading ? '正在刷新…' : '刷新' }}</button></div>
    <div class="oa-tabs" role="tablist" aria-label="企业管理"><button v-for="item in tabs" :id="`oa-tab-${item.id}`" :key="item.id" type="button" role="tab" :aria-selected="tab === item.id" :aria-controls="`oa-panel-${item.id}`" :class="{ active: tab === item.id }" @click="switchTab(item.id)">{{ item.name }}</button></div>
    <p v-if="!tabs.length" class="empty-row">你没有企业管理权限。</p>
    <p v-if="loadError" class="error" role="alert">部分内容加载失败：{{ loadError }}。请点击刷新重试。</p>
    <p v-if="loading" role="status" class="muted">正在加载管理数据…</p>
    <section v-if="tab === 'tenant' && allowed('system.manage')" id="oa-panel-tenant" role="tabpanel" aria-labelledby="oa-tab-tenant" class="oa-company">
      <h2>企业信息</h2><form @submit.prevent="saveCompany"><label>租户编号<input :value="tenant?.id || ''" readonly><small>企业的数据边界，无法在此修改。</small></label><label>企业名称<input v-model="companyName" required maxlength="128" :disabled="loading || companyBusy || !tenant"></label><p v-if="tenant" class="muted">企业状态：{{ tenant.active ? '启用' : '停用' }}</p><p v-if="companyError" class="error" role="alert">{{ companyError }}</p><button class="primary" :disabled="loading || companyBusy || !tenant">{{ companyBusy ? '正在保存…' : '保存企业名称' }}</button></form>
    </section>
    <section v-if="tab === 'organization' && allowed('organization.manage')" id="oa-panel-organization" role="tabpanel" aria-labelledby="oa-tab-organization">
      <div class="oa-toolbar"><label class="oa-search">搜索部门<input v-model="search" type="search" placeholder="输入部门名称"></label><span class="muted">共 {{ units.length }} 个部门</span><button class="primary" :disabled="loading" @click="open('organization', null, $event)">新增部门</button></div>
      <div class="oa-tree-list"><div v-for="unit in filteredTree" :key="unit.id" class="oa-unit-row"><div class="oa-unit-name" :style="{ paddingLeft: `${unit.depth * 22}px` }"><span aria-hidden="true">{{ unit.parent_id ? '└' : '▣' }}</span><strong>{{ unit.name }}</strong><span class="badge" :class="{ ready: unit.active }">{{ unit.active ? '启用' : '停用' }}</span><small>{{ units.filter(child => child.parent_id === unit.id).length }} 个子部门<span v-if="allowed('members.manage')"> · {{ memberCount(unit.id) }} 位直属成员</span></small></div><div class="oa-actions"><button v-if="allowed('members.manage')" class="text-button" @click="selectedUnit = unit.id; switchTab('members')">查看成员</button><button v-if="unit.parent_id" class="text-button" @click="open('organization', unit, $event)">编辑</button><button class="text-button danger" :disabled="!unit.parent_id" :title="!unit.parent_id ? '根部门不可删除' : '删除部门'" @click="open('organization', unit, $event, true)">删除</button></div></div></div>
      <p v-if="!loading && !filteredTree.length" class="empty-row">{{ search ? '没有匹配的部门，请调整搜索条件。' : '尚无部门，请先新增部门。' }}</p>
    </section>
    <section v-if="tab === 'roles' && allowed('roles.manage')" id="oa-panel-roles" role="tabpanel" aria-labelledby="oa-tab-roles">
      <div class="oa-toolbar"><label class="oa-search">搜索角色<input v-model="search" type="search" placeholder="名称或说明"></label><span class="muted">共 {{ roles.length }} 个角色</span><button class="primary" :disabled="loading" @click="open('roles', null, $event)">新增角色</button></div>
      <p class="permission-note">角色决定成员可以执行的系统操作。资料访问范围请在知识库和资料中单独授权。内置角色的权限不可修改；系统会保护最后一位有效管理员。</p>
      <div class="table-scroll"><table><thead><tr><th>角色名称</th><th>系统权限</th><th>状态</th><th>操作</th></tr></thead><tbody><tr v-for="role in visibleRoles" :key="role.id"><td><strong>{{ role.name }}</strong><span v-if="role.builtin" class="badge">内置</span><small>{{ role.description || '暂无说明' }}</small></td><td><div class="oa-tags"><span v-for="permission in role.permissions" :key="permission" class="badge">{{ permissionNames.get(permission) || permission }}</span><span v-if="!role.permissions.length" class="muted">无系统操作权限</span></div></td><td><span class="badge" :class="{ready:role.active}">{{ role.active ? '启用' : '停用' }}</span></td><td><button class="text-button" @click="inspectUsage(role,$event)">使用情况</button><button class="text-button" @click="open('roles', role, $event)">编辑</button><button v-if="!role.builtin" class="text-button danger" @click="open('roles', role, $event, true)">删除</button></td></tr></tbody></table></div><p v-if="!loading && !visibleRoles.length" class="empty-row">{{ search ? '没有匹配的角色。' : '尚无角色，请新增角色。' }}</p>
    </section>
    <section v-if="tab === 'members' && allowed('members.manage')" id="oa-panel-members" role="tabpanel" aria-labelledby="oa-tab-members">
      <div class="oa-toolbar"><label class="oa-search">搜索成员<input v-model="search" type="search" placeholder="账号、姓名或角色"></label><span class="muted">共 {{ members.length }} 位成员</span><button class="primary" :disabled="loading" @click="open('members', null, $event)">新增成员</button></div>
      <div class="oa-members"><aside class="oa-department-filter" aria-label="按部门筛选成员"><h3>所属部门</h3><button type="button" :class="{ active: !selectedUnit }" @click="selectedUnit = ''">全部部门 <small>{{ members.length }} 人</small></button><button v-for="unit in tree" :key="unit.id" type="button" :class="{ active: selectedUnit === unit.id }" :style="{ paddingLeft: `${12 + unit.depth * 15}px` }" @click="selectedUnit = unit.id">{{ unit.name }} <small>{{ memberCount(unit.id) }} 人{{ unit.active ? '' : ' · 停用' }}</small></button><p class="muted">选择部门时包含其下级部门。</p></aside>
      <div class="oa-member-table"><p v-if="selectedUnit" class="muted">当前部门：{{ unitNames.get(selectedUnit) }}（含下级） · {{ visibleMembers.length }} 位成员</p><div class="table-scroll"><table><thead><tr><th>成员</th><th>所属部门</th><th>角色</th><th>状态</th><th>操作</th></tr></thead><tbody><tr v-for="member in visibleMembers" :key="member.id"><td><strong>{{ member.display_name }}</strong><small>{{ member.id }}{{ member.id === me.id ? '（我）' : '' }}</small></td><td>{{ unitNames.get(member.org_unit_id) || '未分配部门' }}</td><td><div class="oa-tags"><span v-for="(name, index) in member.role_names" :key="index" class="badge">{{ name }}</span><span v-if="!member.role_ids.length" class="muted">未分配角色</span></div></td><td><span class="badge" :class="{ ready: member.active }">{{ member.active ? '启用' : '停用' }}</span><small>{{ member.password_enabled ? '密码登录已启用' : '密码登录未启用' }}</small></td><td><button class="text-button" @click="open('members', member, $event)">编辑</button></td></tr></tbody></table></div><p v-if="!loading && !visibleMembers.length" class="empty-row">{{ search || selectedUnit ? '没有匹配的成员，请调整搜索或部门。' : '尚无成员，请新增成员并分配部门与角色。' }}</p></div></div>
    </section>
    <!-- 用途查询与编辑复用弹框，有关联的角色删除改为只读说明。 -->
    <AppDialog v-if="dialog" :title="dialogTitle" :description="dialog.usage ? '列出全部直接引用。资料继承的权限请在所属知识库默认授权中调整。' : dialog.remove ? '删除后无法恢复，请确认所选对象。' : '保存后立即生效。修改自己的密码、角色或停用状态后需要重新登录。'" :return-focus="dialog.returnFocus" :readonly="!!dialog.usage || (dialog.kind==='roles'&&dialog.remove&&roleReferences.length>0)" :busy="saving" :danger="dialog.remove" :submit-label="dialog.remove ? '确认删除' : '保存'" @close="close" @submit="submit">
      <div class="oa-dialog-fields"><template v-if="dialog.usage"><p v-if="referenceLoading" role="status">正在查询关联信息…</p><p v-else-if="referencesLoaded" class="permission-note">成员 {{ roleReferences.filter(item=>item.type==='member').length }} 人，知识库 {{ roleReferences.filter(item=>item.type==='space').length }} 个，资料 {{ roleReferences.filter(item=>item.type==='document').length }} 份。</p><p v-if="referencesLoaded&&!roleReferences.length">当前没有直接引用此角色的成员、知识库或资料。</p></template><template v-else-if="dialog.remove"><strong>{{ form.name }}</strong><p>{{ dialog.kind === 'organization' ? '根部门、有下级部门或成员的部门不能删除。请先迁移成员并处理下级部门。' : '仍有成员使用的角色不能删除，请先调整成员角色。' }}</p></template>
      <template v-else-if="dialog.kind === 'organization'"><fieldset><legend>部门信息</legend><label>部门名称<input v-model="form.name" required maxlength="128"></label><label>上级部门<select v-model="form.parent_id" :disabled="dialog.editing && !dialog.original.parent_id"><option value="">无上级（根部门）</option><option v-for="unit in parentOptions" :key="unit.id" :value="unit.id">{{ '　'.repeat(unit.depth) }}{{ unit.name }}{{ unit.active ? '' : '（停用）' }}</option></select></label><label class="inline"><input v-model="form.active" type="checkbox">启用部门</label><small>部门仅用于组织归属，不会自动赋予资料访问权。</small></fieldset></template>
      <template v-else-if="dialog.kind === 'roles'"><fieldset><legend>角色信息</legend><label>角色名称<input v-model="form.name" required maxlength="128"></label><label>角色说明<textarea v-model="form.description" rows="3" maxlength="2000"></textarea></label><label class="inline"><input v-model="form.active" type="checkbox" :disabled="form.builtin">启用角色</label></fieldset><fieldset><legend>系统操作权限</legend><p v-if="form.builtin" class="muted">内置角色的权限固定，不能修改。</p><label v-for="permission in catalog" :key="permission.id" class="oa-permission"><input v-model="form.permissions" type="checkbox" :value="permission.id" :disabled="form.builtin"><span>{{ permission.label }}<small>{{ permission.description }}</small></span></label><p v-if="!catalog.length" class="muted">权限目录未加载，请关闭弹框后刷新重试。</p></fieldset></template>
      <template v-else-if="dialog.kind === 'members'"><fieldset><legend>账号与身份</legend><label>成员账号<input v-model="form.id" required :readonly="dialog.editing" maxlength="128" autocomplete="off"><small>创建后无法更改。</small></label><label>显示姓名<input v-model="form.display_name" required maxlength="128" autocomplete="off"></label><label>所属部门<select v-model="form.org_unit_id" required><option disabled value="">请选择部门</option><option v-for="unit in selectableUnits" :key="unit.id" :value="unit.id">{{ '　'.repeat(unit.depth) }}{{ unit.name }}{{ unit.active ? '' : '（停用）' }}</option></select></label><label class="inline"><input v-model="form.active" type="checkbox">启用成员</label></fieldset><fieldset><legend>角色分配（可多选）</legend><label v-for="role in selectableRoles" :key="role.id" class="oa-permission"><input v-model="form.role_ids" type="checkbox" :value="role.id" :disabled="!role.active && !form.role_ids.includes(role.id)"><span>{{ role.name }}{{ role.active ? '' : '（已停用，不授予权限）' }}<small>{{ role.description }}</small></span></label><p v-if="!selectableRoles.length" class="muted">暂无启用的角色，请先在角色管理中配置。</p><small>系统会阻止停用或移除最后一位有效管理员。</small></fieldset><fieldset><legend>{{ dialog.editing ? '重置密码' : '初始密码' }}</legend><label>{{ dialog.editing ? '新密码（留空保留当前密码）' : '设置密码' }}<input v-model="form.password" type="password" autocomplete="new-password" minlength="12" maxlength="128" :required="!dialog.editing"><small>至少 12 位。请通过可信渠道告知成员。</small></label></fieldset></template>
      <p v-if="dialog.kind==='roles'&&dialog.remove&&referenceLoading" role="status">正在查询关联信息…</p><p v-if="formError" class="error" role="alert">{{ formError }}</p>
      <div v-if="roleReferences.length" class="oa-role-references"><h3>正在使用此角色的对象</h3><p>先到下列位置移除或替换角色，再回来删除。停用成员和已撤回资料中的关联也会列出。</p><div class="table-scroll"><table><thead><tr><th>类型与名称</th><th>使用方式</th><th>调整位置</th></tr></thead><tbody><tr v-for="item in roleReferences" :key="item.type+item.id"><td><strong>{{ {member:'成员',space:'知识库',document:'资料'}[item.type] }}：{{ item.name }}</strong><small>{{ item.id }}</small><small v-if="item.type==='member'&&!item.active">已停用或成员不存在</small><small v-if="item.withdrawn">已撤回</small></td><td>{{ item.type==='member'?'角色分配':item.rules.map(rule=>rule==='allow'?'允许阅读':'明确禁止').join('、') }}</td><td>{{ item.location }}</td></tr></tbody></table></div></div></div>
    </AppDialog>
  </section>
</template>
