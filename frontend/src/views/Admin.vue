<template>
  <div class="admin-layout">
    <header class="admin-header">
      <h1>管理后台</h1>
      <AppNavigation />
    </header>

    <div class="admin-content">
      <p v-if="error" role="alert" class="request-error">{{ error }}</p>
      <div class="tabs">
        <button :disabled="busy" :class="{ active: tab === 'llm' }" @click="tab = 'llm'">模型配置</button>
        <button :disabled="busy" :class="{ active: tab === 'db' }" @click="tab = 'db'">数据库连接</button>
        <button :disabled="busy" :class="{ active: tab === 'users' }" @click="tab = 'users'">用户管理</button>
      </div>

      <div v-if="tab === 'llm'" class="tab-content">
        <button :disabled="busy" @click="showLLMForm = true">添加模型配置</button>
        <div v-if="showLLMForm" class="form-modal">
          <input v-model="llmForm.name" placeholder="名称" />
          <input v-model="llmForm.provider" placeholder="Provider" />
          <input v-model="llmForm.model_string" placeholder="模型名" />
          <input v-model="llmForm.api_key" placeholder="API Key" type="password" />
          <input v-model="llmForm.base_url" placeholder="Base URL" />
          <div class="modal-actions">
            <button :disabled="busy" @click="createLLM">创建</button>
            <button :disabled="busy" @click="showLLMForm = false">取消</button>
          </div>
        </div>
        <div v-for="cfg in llmConfigs" :key="cfg.id" class="item-card">
          <span>{{ cfg.name }} - {{ cfg.model_string }}</span>
          <span v-if="cfg.is_default">默认</span>
          <button :disabled="busy" @click="setDefault(cfg.id)">设为默认</button>
          <button :disabled="busy" @click="testLLM(cfg.id)">测试</button>
          <button :disabled="busy" @click="deleteLLM(cfg.id)">删除</button>
        </div>
      </div>

      <div v-if="tab === 'db'" class="tab-content">
        <button :disabled="busy" @click="showDBForm = true">添加数据库连接</button>
        <div v-if="showDBForm" class="form-modal">
          <input v-model="dbForm.name" placeholder="名称" />
          <input v-model="dbForm.db_type" placeholder="类型(mysql/postgresql/mssql/oracle/mongodb)" />
          <input v-model="dbForm.host" placeholder="Host" />
          <input v-model.number="dbForm.port" placeholder="Port" type="number" />
          <input v-model="dbForm.user" placeholder="User" />
          <input v-model="dbForm.password" placeholder="Password" type="password" />
          <input v-model="dbForm.database" placeholder="Database" />
          <div class="modal-actions">
            <button :disabled="busy" @click="createDB">创建</button>
            <button :disabled="busy" @click="showDBForm = false">取消</button>
          </div>
        </div>
        <div v-for="conn in dbConnections" :key="conn.id" class="item-card">
          <span>{{ conn.name }} - {{ conn.db_type }}</span>
          <span :class="{ active: conn.is_active }">{{ conn.is_active ? '活跃' : '未激活' }}</span>
          <button :disabled="busy" @click="testDB(conn.id)">测试</button>
          <button :disabled="busy" @click="refreshSchema(conn.id)">刷新Schema</button>
          <button :disabled="busy" @click="deleteDB(conn.id)">删除</button>
        </div>
      </div>

      <div v-if="tab === 'users'" class="tab-content">
        <button :disabled="busy" @click="showUserForm = true">添加用户</button>
        <div v-if="showUserForm" class="form-modal">
          <input v-model="userForm.username" placeholder="用户名" />
          <input v-model="userForm.password" placeholder="密码" type="password" />
          <select v-model="userForm.role">
            <option value="user">用户</option>
            <option value="admin">管理员</option>
          </select>
          <div class="modal-actions">
            <button :disabled="busy" @click="createUser">创建</button>
            <button :disabled="busy" @click="showUserForm = false">取消</button>
          </div>
        </div>
        <div v-for="u in users" :key="u.id" class="item-card">
          <span>{{ u.username }} ({{ u.role }})</span>
          <span>{{ u.is_active ? '激活' : '禁用' }}</span>
          <button :disabled="busy" @click="toggleUser(u.id)">切换状态</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { errorMessage } from '@/utils/errors'
import AppNavigation from '@/components/AppNavigation.vue'
import * as adminApi from '@/api/admin'

const busy = ref(false)
const error = ref('')
async function run(action) {
  if (busy.value) return
  busy.value = true
  error.value = ''
  try { await action() } catch (e) { error.value = errorMessage(e) } finally { busy.value = false }
}

const tab = ref('llm')
const llmConfigs = ref([])
const dbConnections = ref([])
const users = ref([])

const showLLMForm = ref(false)
const showDBForm = ref(false)
const showUserForm = ref(false)

const llmForm = ref({ name: '', provider: '', model_string: '', api_key: '', base_url: '' })
const dbForm = ref({ name: '', db_type: '', host: '', port: 3306, user: '', password: '', database: '' })
const userForm = ref({ username: '', password: '', role: 'user' })

async function loadData() {
  llmConfigs.value = await adminApi.getLLMConfigs()
  dbConnections.value = await adminApi.getDBConnections()
  users.value = await adminApi.getUsers()
}

async function createLLM() {
  await run(async () => {
    if (!llmForm.value.name.trim() || !llmForm.value.provider.trim() || !llmForm.value.model_string.trim()) throw new Error('请填写名称、Provider和模型名')
    await adminApi.createLLMConfig(llmForm.value)
    showLLMForm.value = false
    llmForm.value = { name: '', provider: '', model_string: '', api_key: '', base_url: '' }
    await loadData()
  })
}

async function testLLM(id) {
  await run(async () => {
    const result = await adminApi.testLLMConfig(id)
    alert(result.message || '连接成功')
  })
}

async function setDefault(id) {
  await run(async () => {
    await adminApi.setDefaultLLMConfig(id)
    await loadData()
  })
}

async function deleteLLM(id) {
  await run(async () => {
    await adminApi.deleteLLMConfig(id)
    await loadData()
  })
}

async function createDB() {
  await run(async () => {
    if (!dbForm.value.name.trim() || !dbForm.value.db_type.trim() || !dbForm.value.host.trim() || !dbForm.value.database.trim()) throw new Error('请填写名称、类型、Host和Database')
    if (!Number.isInteger(dbForm.value.port) || dbForm.value.port < 1 || dbForm.value.port > 65535) throw new Error('请输入1到65535之间的端口')
    await adminApi.createDBConnection(dbForm.value)
    showDBForm.value = false
    dbForm.value = { name: '', db_type: '', host: '', port: 3306, user: '', password: '', database: '' }
    await loadData()
  })
}

async function testDB(id) {
  await run(async () => {
    await adminApi.testDBConnection(id)
    alert('连接成功')
  })
}

async function refreshSchema(id) {
  await run(async () => {
    await adminApi.refreshSchema(id)
    alert('Schema已刷新')
  })
}

async function deleteDB(id) {
  await run(async () => {
    await adminApi.deleteDBConnection(id)
    await loadData()
  })
}

async function createUser() {
  await run(async () => {
    if (!userForm.value.username.trim() || !userForm.value.password) throw new Error('请填写用户名和密码')
    await adminApi.createUser(userForm.value.username, userForm.value.password, userForm.value.role)
    showUserForm.value = false
    userForm.value = { username: '', password: '', role: 'user' }
    await loadData()
  })
}

async function toggleUser(id) {
  await run(async () => {
    await adminApi.toggleUserActive(id)
    await loadData()
  })
}

onMounted(() => run(loadData))
</script>

<style scoped>
.request-error { color: #c0392b; padding: 12px 20px; position: relative; z-index: 101; background: white; }
button:disabled { opacity: 0.6; cursor: wait; }
.admin-layout { min-height: 100vh; }

.admin-header {
  display: flex;
  justify-content: space-between;
  padding: 16px 24px;
  background: white;
  border-bottom: 1px solid #eee;
}

.tabs {
  display: flex;
  background: #f5f5f5;
  padding: 10px;
  gap: 10px;
}

.tabs button {
  padding: 10px 20px;
  border: none;
  background: transparent;
  cursor: pointer;
  border-radius: 4px;
}

.tabs button.active {
  background: white;
  box-shadow: 0 2px 4px rgba(0,0,0,0.1);
}

.tab-content { padding: 20px; }

.form-modal {
  background: white;
  padding: 20px;
  border-radius: 8px;
  margin: 15px 0;
  box-shadow: 0 2px 8px rgba(0,0,0,0.1);
}

.form-modal input, .form-modal select {
  width: 100%;
  padding: 10px;
  margin: 10px 0;
  border: 1px solid #ddd;
  border-radius: 4px;
}

.item-card {
  display: flex;
  align-items: center;
  gap: 15px;
  padding: 15px;
  background: white;
  margin: 10px 0;
  border-radius: 4px;
  box-shadow: 0 2px 4px rgba(0,0,0,0.05);
}

.item-card button {
  padding: 6px 12px;
  border: 1px solid #ddd;
  background: #f5f5f5;
  border-radius: 4px;
  cursor: pointer;
}

.modal-actions {
  display: flex;
  gap: 10px;
  margin-top: 15px;
}

.modal-actions button {
  flex: 1;
  padding: 10px;
  cursor: pointer;
}
</style>
