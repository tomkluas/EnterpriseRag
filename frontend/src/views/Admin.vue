<template>
  <div class="chat-layout">
    <ChatSidebar />
    <div class="admin-main">
      <div class="admin-header"><h2>⚙️ 系统管理</h2></div>
      <div class="tabs">
        <button v-for="t in tabs" :key="t.key" :class="{ active: activeTab === t.key }" @click="activeTab = t.key">
          {{ t.label }}
        </button>
      </div>

      <!-- LLM Configs -->
      <div v-if="activeTab === 'llm'" class="tab-content">
        <div class="section-header">
          <h3>模型配置</h3>
          <button @click="showLLMForm = true">＋ 新增</button>
        </div>
        <table>
          <thead><tr><th>名称</th><th>提供商</th><th>模型</th><th>默认</th><th>操作</th></tr></thead>
          <tbody>
            <tr v-for="c in llmConfigs" :key="c.id">
              <td>{{ c.name }}</td>
              <td>{{ c.provider }}</td>
              <td>{{ c.model_string }}</td>
              <td>{{ c.is_default ? '✅' : '' }}</td>
              <td class="actions">
                <button @click="setDefault(c.id)">设为默认</button>
                <button @click="testLLM(c.id)">测试</button>
                <button class="danger" @click="deleteLLM(c.id)">删除</button>
              </td>
            </tr>
          </tbody>
        </table>

        <!-- LLM Form Modal -->
        <div v-if="showLLMForm" class="modal-overlay" @click.self="showLLMForm = false">
          <div class="modal">
            <h3>新增模型配置</h3>
            <label>显示名称</label><input v-model="llmForm.name" placeholder="例：日常问答用" />
            <label>提供商</label>
            <select v-model="llmForm.provider" @change="onLLMProviderChange">
              <option value="zhipu">智谱AI</option>
              <option value="qwen">通义千问</option>
              <option value="deepseek">DeepSeek</option>
              <option value="doubao">豆包</option>
              <option value="moonshot">月之暗面</option>
            </select>
            <label>模型标识</label><input v-model="llmForm.model_string" placeholder="留空使用推荐模型" />
            <label>API Key</label><input v-model="llmForm.api_key" type="password" placeholder="粘贴您的 API Key" />
            <label>最大 Token</label><input v-model.number="llmForm.max_tokens" type="number" />
            <label>Temperature</label><input v-model.number="llmForm.temperature" type="number" step="0.1" min="0" max="2" />
            <div class="modal-actions">
              <button @click="showLLMForm = false">取消</button>
              <button class="primary" @click="saveLLM">保存</button>
            </div>
          </div>
        </div>
      </div>

      <!-- DB Connections -->
      <div v-if="activeTab === 'db'" class="tab-content">
        <div class="section-header">
          <h3>数据库连接</h3>
          <button @click="showDBForm = true">＋ 新增</button>
        </div>
        <table>
          <thead><tr><th>名称</th><th>类型</th><th>Schema更新</th><th>操作</th></tr></thead>
          <tbody>
            <tr v-for="c in dbConnections" :key="c.id">
              <td>{{ c.name }}</td>
              <td>{{ c.db_type }}</td>
              <td>{{ c.schema_updated ? c.schema_updated.slice(0, 10) : '-' }}</td>
              <td class="actions">
                <button @click="testDB(c.id)">测试</button>
                <button @click="refreshSchema(c.id)">刷新 Schema</button>
                <button class="danger" @click="deleteDB(c.id)">删除</button>
              </td>
            </tr>
          </tbody>
        </table>

        <!-- DB Form Modal -->
        <div v-if="showDBForm" class="modal-overlay" @click.self="showDBForm = false">
          <div class="modal">
            <h3>新增数据库连接</h3>
            <label>名称</label><input v-model="dbForm.name" placeholder="业务数据库" />
            <label>类型</label>
            <select v-model="dbForm.db_type">
              <option value="mysql">MySQL</option>
              <option value="postgresql">PostgreSQL</option>
              <option value="mssql">SQL Server</option>
              <option value="oracle">Oracle</option>
              <option value="mongodb">MongoDB</option>
            </select>
            <label>主机</label><input v-model="dbForm.host" placeholder="localhost" />
            <label>端口</label><input v-model.number="dbForm.port" type="number" />
            <label>用户名</label><input v-model="dbForm.user" />
            <label>密码</label><input v-model="dbForm.password" type="password" />
            <label>数据库名</label><input v-model="dbForm.database" />
            <div class="modal-actions">
              <button @click="showDBForm = false">取消</button>
              <button class="primary" @click="saveDB">保存</button>
            </div>
          </div>
        </div>
      </div>

      <!-- Users -->
      <div v-if="activeTab === 'users'" class="tab-content">
        <div class="section-header">
          <h3>用户管理</h3>
          <button @click="showUserForm = true">＋ 新增用户</button>
        </div>
        <table>
          <thead><tr><th>用户名</th><th>角色</th><th>状态</th><th>创建时间</th><th>操作</th></tr></thead>
          <tbody>
            <tr v-for="u in users" :key="u.id">
              <td>{{ u.username }}</td>
              <td>{{ u.role }}</td>
              <td><span :class="u.is_active ? 'active-badge' : 'inactive-badge'">{{ u.is_active ? '启用' : '禁用' }}</span></td>
              <td>{{ u.created_at?.slice(0, 10) }}</td>
              <td class="actions">
                <button @click="toggleUser(u.id)">{{ u.is_active ? '禁用' : '启用' }}</button>
              </td>
            </tr>
          </tbody>
        </table>

        <div v-if="showUserForm" class="modal-overlay" @click.self="showUserForm = false">
          <div class="modal">
            <h3>新增用户</h3>
            <label>用户名</label><input v-model="userForm.username" />
            <label>密码</label><input v-model="userForm.password" type="password" />
            <label>角色</label>
            <select v-model="userForm.role">
              <option value="user">普通用户</option>
              <option value="admin">管理员</option>
            </select>
            <div class="modal-actions">
              <button @click="showUserForm = false">取消</button>
              <button class="primary" @click="saveUser">保存</button>
            </div>
          </div>
        </div>
      </div>

      <!-- System Settings -->
      <div v-if="activeTab === 'system'" class="tab-content">
        <div class="section-header">
          <h3>Embedding 服务（知识库检索依赖）</h3>
        </div>
        <div class="settings-body">
          <p class="current-status">
            当前：{{ embeddingInfo.configured ? `${providerLabel(embeddingInfo.provider)} / ${embeddingInfo.model}（已配置）` : '未配置' }}
          </p>
          <label>服务商</label>
          <select v-model="embeddingForm.provider" @change="embeddingForm.model = ''; embeddingForm.api_key = ''">
            <option value="zhipu">智谱AI</option>
            <option value="qwen">通义千问</option>
          </select>
          <label>模型名（留空使用默认）</label>
          <input v-model="embeddingForm.model" :placeholder="embeddingForm.provider === 'qwen' ? 'text-embedding-v3（默认）' : 'embedding-3（默认）'" />
          <label>API Key（留空表示不修改现有配置）</label>
          <input v-model="embeddingForm.api_key" type="password" placeholder="重新输入以更新" />
          <button class="primary" style="margin-top:10px" @click="saveEmbeddingConfig">保存</button>
        </div>

        <div class="section-header" style="margin-top:24px">
          <h3>联网搜索服务（可选）</h3>
        </div>
        <div class="settings-body">
          <p class="current-status">
            当前：{{ searchInfo.configured ? `${providerLabel(searchInfo.provider)}（已配置）` : '未配置' }}
          </p>
          <label>服务商</label>
          <select v-model="searchForm.provider">
            <option value="bocha">博查</option>
            <option value="tavily">Tavily</option>
          </select>
          <label>API Key（留空表示不修改现有配置）</label>
          <input v-model="searchForm.api_key" type="password" placeholder="重新输入以更新" />
          <button class="primary" style="margin-top:10px" @click="saveSearchConfig">保存</button>
        </div>
      </div>

      <!-- Status message -->
      <div v-if="statusMsg" class="status-toast" :class="statusType">{{ statusMsg }}</div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import ChatSidebar from '@/components/ChatSidebar.vue'
import { adminApi } from '@/api/admin.js'

const activeTab = ref('llm')
const tabs = [
  { key: 'llm', label: '🤖 模型配置' },
  { key: 'db', label: '🗄️ 数据库连接' },
  { key: 'users', label: '👥 用户管理' },
  { key: 'system', label: '⚙️ 系统设置' },
]

const llmConfigs = ref([])
const dbConnections = ref([])
const users = ref([])
const showLLMForm = ref(false)
const showDBForm = ref(false)
const showUserForm = ref(false)
const statusMsg = ref('')
const statusType = ref('ok')

const llmForm = ref({ name: '', provider: 'zhipu', model_string: '', api_key: '', max_tokens: 4096, temperature: 0.7 })
const dbForm = ref({ name: '', db_type: 'mysql', host: 'localhost', port: 3306, user: '', password: '', database: '' })
const userForm = ref({ username: '', password: '', role: 'user' })

const embeddingForm = ref({ provider: 'zhipu', model: '', api_key: '' })
const searchForm = ref({ provider: 'bocha', api_key: '' })
const embeddingInfo = ref({ provider: '', configured: false })
const searchInfo = ref({ provider: '', configured: false })

const PROVIDER_LABELS = { zhipu: '智谱AI', qwen: '通义千问', deepseek: 'DeepSeek', doubao: '豆包', moonshot: '月之暗面', bocha: '博查', tavily: 'Tavily' }
function providerLabel(key) { return PROVIDER_LABELS[key] || key || '-' }

function onLLMProviderChange() {
  llmForm.value.model_string = ''   // 切换服务商时清空，后端会用该服务商的推荐默认模型
}

onMounted(async () => {
  await Promise.all([loadLLM(), loadDB(), loadUsers(), loadEmbeddingInfo(), loadSearchInfo()])
})

async function loadLLM() { llmConfigs.value = await adminApi.getLLMConfigs() }
async function loadDB() { dbConnections.value = await adminApi.getDBConnections() }
async function loadUsers() { users.value = await adminApi.getUsers() }
async function loadEmbeddingInfo() {
  const info = await adminApi.getEmbeddingConfig()
  embeddingInfo.value = info
  if (info.provider) embeddingForm.value.provider = info.provider
  embeddingForm.value.model = info.model || ''
}
async function loadSearchInfo() {
  const info = await adminApi.getSearchConfig()
  searchInfo.value = info
  if (info.provider) searchForm.value.provider = info.provider
}

async function saveEmbeddingConfig() {
  if (!embeddingForm.value.api_key.trim() && (!embeddingInfo.value.configured || embeddingForm.value.provider !== embeddingInfo.value.provider)) {
    toast('请输入 API Key', 'err')
    return
  }
  try {
    await adminApi.updateEmbeddingConfig({ ...embeddingForm.value })
    await loadEmbeddingInfo()
    embeddingForm.value.api_key = ''
    toast('已保存')
  } catch (e) {
    toast(e.response?.data?.detail || '保存失败', 'err')
  }
}

async function saveSearchConfig() {
  if (!searchForm.value.api_key) {
    toast('请输入 API Key', 'err')
    return
  }
  try {
    await adminApi.updateSearchConfig({ ...searchForm.value })
    await loadSearchInfo()
    searchForm.value.api_key = ''
    toast('已保存')
  } catch (e) {
    toast(e.response?.data?.detail || '保存失败', 'err')
  }
}

function toast(msg, type = 'ok') {
  statusMsg.value = msg; statusType.value = type
  setTimeout(() => statusMsg.value = '', 3000)
}

// LLM
async function saveLLM() {
  try { await adminApi.createLLMConfig({ ...llmForm.value }); showLLMForm.value = false; await loadLLM(); toast('已保存') }
  catch (e) { toast(e.response?.data?.detail || '保存失败', 'err') }
}
async function setDefault(id) { await adminApi.setDefaultLLM(id); await loadLLM(); toast('已设为默认') }
async function testLLM(id) {
  toast('测试中...')
  try { const r = await adminApi.testLLMConfig(id); toast(r.ok ? '连接成功' : '失败') }
  catch (e) { toast('连接失败: ' + (e.response?.data?.detail || ''), 'err') }
}
async function deleteLLM(id) {
  if (!confirm('确认删除？')) return
  await adminApi.deleteLLMConfig(id); await loadLLM(); toast('已删除')
}

// DB
async function saveDB() {
  try { await adminApi.createDBConnection({ ...dbForm.value }); showDBForm.value = false; await loadDB(); toast('已保存') }
  catch (e) { toast(e.response?.data?.detail || '保存失败', 'err') }
}
async function testDB(id) {
  toast('测试中...')
  try { const r = await adminApi.testDBConnection(id); toast(r.ok ? '连接成功' : '失败') }
  catch (e) { toast('连接失败: ' + (e.response?.data?.detail || ''), 'err') }
}
async function refreshSchema(id) {
  try { const r = await adminApi.refreshSchema(id); toast(`Schema 已更新，共 ${r.tables?.length ?? 0} 张表`) }
  catch (e) { toast('刷新失败', 'err') }
}
async function deleteDB(id) {
  if (!confirm('确认删除？')) return
  await adminApi.deleteDBConnection(id); await loadDB(); toast('已删除')
}

// Users
async function saveUser() {
  try { await adminApi.createUser({ ...userForm.value }); showUserForm.value = false; await loadUsers(); toast('用户已创建') }
  catch (e) { toast(e.response?.data?.detail || '保存失败', 'err') }
}
async function toggleUser(id) {
  await adminApi.toggleUserActive(id); await loadUsers()
}
</script>

<style scoped>
.chat-layout { display: flex; height: 100vh; overflow: hidden; }
.admin-main { flex: 1; overflow-y: auto; padding: 24px; background: #f9fafb; }
.admin-header { margin-bottom: 16px; }
h2 { font-size: 1.2rem; font-weight: 600; }
.tabs { display: flex; gap: 4px; margin-bottom: 20px; border-bottom: 1px solid #e5e7eb; }
.tabs button {
  padding: 8px 18px; background: none; border: none; border-bottom: 2px solid transparent;
  cursor: pointer; font-size: 0.9rem; color: #6b7280;
}
.tabs button.active { color: #3b82f6; border-bottom-color: #3b82f6; font-weight: 500; }
.tab-content { background: white; border-radius: 10px; border: 1px solid #e5e7eb; overflow: hidden; }
.section-header {
  display: flex; align-items: center; justify-content: space-between;
  padding: 14px 16px; border-bottom: 1px solid #e5e7eb;
}
h3 { font-size: 1rem; font-weight: 600; }
.section-header button {
  padding: 7px 14px; background: #3b82f6; color: white;
  border: none; border-radius: 8px; cursor: pointer; font-size: 0.85rem;
}
table { width: 100%; border-collapse: collapse; font-size: 0.88rem; }
th { padding: 10px 14px; text-align: left; color: #6b7280; background: #f9fafb; font-weight: 500; }
td { padding: 10px 14px; border-top: 1px solid #f3f4f6; }
.actions { display: flex; gap: 6px; }
.actions button {
  padding: 4px 10px; font-size: 0.8rem; border-radius: 6px;
  border: 1px solid #d1d5db; background: white; cursor: pointer; color: #374151;
}
.actions button:hover { background: #f3f4f6; }
.actions button.danger { color: #ef4444; border-color: #fca5a5; }
.active-badge { color: #22c55e; font-weight: 500; }
.inactive-badge { color: #9ca3af; }
.modal-overlay {
  position: fixed; inset: 0; background: rgba(0,0,0,0.4);
  display: flex; align-items: center; justify-content: center; z-index: 100;
}
.modal {
  background: white; border-radius: 12px; padding: 28px; width: 400px;
  display: flex; flex-direction: column; gap: 8px; max-height: 90vh; overflow-y: auto;
}
.modal h3 { font-size: 1rem; font-weight: 600; margin-bottom: 4px; }
.modal label { font-size: 0.83rem; color: #374151; font-weight: 500; }
.modal input, .modal select {
  padding: 8px 10px; border: 1px solid #d1d5db; border-radius: 7px; font-size: 0.9rem;
}
.modal-actions { display: flex; gap: 8px; justify-content: flex-end; margin-top: 8px; }
.settings-body { padding: 16px; display: flex; flex-direction: column; gap: 8px; max-width: 360px; }
.settings-body label { font-size: 0.83rem; color: #374151; font-weight: 500; margin-top: 4px; }
.settings-body input, .settings-body select {
  padding: 8px 10px; border: 1px solid #d1d5db; border-radius: 7px; font-size: 0.9rem;
}
.current-status { font-size: 0.85rem; color: #6b7280; margin-bottom: 4px; }
.modal-actions button {
  padding: 7px 16px; border-radius: 8px; border: 1px solid #d1d5db;
  background: white; cursor: pointer; font-size: 0.88rem;
}
.modal-actions button.primary { background: #3b82f6; color: white; border-color: #3b82f6; }
.status-toast {
  position: fixed; bottom: 24px; right: 24px; padding: 10px 20px;
  background: #22c55e; color: white; border-radius: 8px; font-size: 0.9rem;
  box-shadow: 0 4px 12px rgba(0,0,0,0.15); z-index: 200;
}
.status-toast.err { background: #ef4444; }
</style>
