<template>
  <div class="chat-layout">
    <ChatSidebar />
    <div class="kb-main">
      <div class="kb-header">
        <h2>📚 知识库管理</h2>
        <button @click="showCreate = true">＋ 新建知识库</button>
      </div>

      <!-- Create Dialog -->
      <div v-if="showCreate" class="modal-overlay" @click.self="showCreate = false">
        <div class="modal">
          <h3>新建知识库</h3>
          <label>名称</label>
          <input v-model="newName" placeholder="知识库名称" />
          <label>描述</label>
          <input v-model="newDesc" placeholder="可选描述" />
          <div class="modal-actions">
            <button @click="showCreate = false">取消</button>
            <button class="primary" @click="handleCreate">创建</button>
          </div>
        </div>
      </div>

      <div class="kb-grid">
        <div v-for="kb in kbs" :key="kb.id" class="kb-card" :class="{ selected: selected?.id === kb.id }" @click="selectKb(kb)">
          <div class="kb-card-header">
            <span class="kb-name">{{ kb.name }}</span>
            <span class="kb-count">{{ kb.doc_count }} 文档</span>
          </div>
          <p class="kb-desc">{{ kb.description || '暂无描述' }}</p>
          <button class="del-kb" @click.stop="handleDeleteKb(kb.id)">删除</button>
        </div>
      </div>

      <div v-if="selected" class="docs-panel">
        <div class="docs-header">
          <h3>{{ selected.name }} — 文档列表</h3>
          <label class="upload-btn">
            📎 上传文档
            <input type="file" accept=".pdf,.docx,.xlsx,.txt,.md" @change="handleUpload" hidden />
          </label>
        </div>
        <p v-if="refreshError" class="doc-error">{{ refreshError }}</p>
        <table>
          <thead>
            <tr><th>文件名</th><th>状态</th><th>块数</th><th>大小</th><th>操作</th></tr>
          </thead>
          <tbody>
            <tr v-for="doc in docs" :key="doc.id">
              <td>{{ doc.filename }}</td>
              <td>
                <span :class="`status-${doc.status}`">{{ statusLabel(doc.status) }}</span>
                <p v-if="doc.status === 'failed' && doc.error_msg" class="doc-error">{{ doc.error_msg }}</p>
              </td>
              <td>{{ doc.chunk_count }}</td>
              <td>{{ formatSize(doc.file_size) }}</td>
              <td><button class="del-doc" @click="handleDeleteDoc(doc.id)">删除</button></td>
            </tr>
            <tr v-if="docs.length === 0">
              <td colspan="5" style="text-align:center;color:#9ca3af">暂无文档</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import ChatSidebar from '@/components/ChatSidebar.vue'
import { knowledgeApi } from '@/api/knowledge.js'

const kbs = ref([])
const selected = ref(null)
const docs = ref([])
const showCreate = ref(false)
const newName = ref('')
const newDesc = ref('')
const refreshError = ref('')
let pollTimer
let polling = false
let disposed = false

onMounted(() => {
  loadKbs().catch(() => { refreshError.value = '知识库列表加载失败，请刷新页面重试' })
  pollTimer = setInterval(async () => {
    if (disposed || polling || !selected.value || !docs.value.some(doc => ['pending', 'processing'].includes(doc.status))) return
    polling = true
    try {
      await refreshDocs()
      if (!disposed && !docs.value.some(doc => ['pending', 'processing'].includes(doc.status))) await loadKbs()
    } catch {
      if (!disposed) refreshError.value = '状态刷新失败，正在重试；当前显示的状态可能已过期'
    } finally {
      polling = false
    }
  }, 2000)
})

onUnmounted(() => {
  disposed = true
  clearInterval(pollTimer)
})

async function loadKbs() {
  kbs.value = await knowledgeApi.list()
}

async function selectKb(kb) {
  selected.value = kb
  docs.value = []
  await refreshDocs()
}

async function handleCreate() {
  if (!newName.value.trim()) return
  await knowledgeApi.create(newName.value.trim(), newDesc.value)
  showCreate.value = false
  newName.value = ''
  newDesc.value = ''
  await loadKbs()
}

async function handleDeleteKb(id) {
  if (!confirm('确认删除该知识库及全部文档？')) return
  await knowledgeApi.deleteKb(id)
  if (selected.value?.id === id) selected.value = null
  await loadKbs()
}

async function handleUpload(e) {
  const file = e.target.files[0]
  if (!file || !selected.value) return
  await knowledgeApi.upload(selected.value.id, file)
  e.target.value = ''
  await refreshDocs()
}

async function handleDeleteDoc(docId) {
  if (!confirm('确认删除该文档？')) return
  await knowledgeApi.deleteDoc(selected.value.id, docId)
  await refreshDocs()
}

async function refreshDocs() {
  const kbId = selected.value?.id
  if (kbId) {
    const latestDocs = await knowledgeApi.listDocs(kbId)
    if (!disposed && selected.value?.id === kbId) {
      docs.value = latestDocs
      refreshError.value = ''
    }
  }
}

function statusLabel(status) {
  return { pending: '排队中', processing: '处理中（解析 / 向量化 / 入库）', done: '已完成', failed: '处理失败' }[status] || status
}

function formatSize(bytes) {
  if (!bytes) return '-'
  if (bytes < 1024) return `${bytes}B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`
  return `${(bytes / 1024 / 1024).toFixed(1)}MB`
}
</script>

<style scoped>
.chat-layout { display: flex; height: 100vh; overflow: hidden; }
.kb-main { flex: 1; overflow-y: auto; padding: 24px; background: #f9fafb; }
.kb-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 20px; }
h2 { font-size: 1.2rem; font-weight: 600; }
.kb-header button {
  padding: 8px 16px; background: #3b82f6; color: white;
  border: none; border-radius: 8px; cursor: pointer; font-size: 0.9rem;
}
.kb-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 14px; margin-bottom: 28px; }
.kb-card {
  background: white; border: 1px solid #e5e7eb; border-radius: 10px;
  padding: 14px; cursor: pointer; transition: box-shadow 0.15s;
}
.kb-card:hover { box-shadow: 0 2px 8px rgba(0,0,0,0.08); }
.kb-card.selected { border-color: #3b82f6; box-shadow: 0 0 0 2px #bfdbfe; }
.kb-card-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; }
.kb-name { font-weight: 600; font-size: 0.95rem; }
.kb-count { font-size: 0.78rem; color: #6b7280; background: #f3f4f6; padding: 2px 7px; border-radius: 10px; }
.kb-desc { font-size: 0.82rem; color: #9ca3af; margin-bottom: 10px; }
.del-kb { font-size: 0.78rem; color: #ef4444; background: none; border: none; cursor: pointer; }
.docs-panel { background: white; border-radius: 10px; border: 1px solid #e5e7eb; overflow: hidden; }
.docs-header {
  display: flex; align-items: center; justify-content: space-between;
  padding: 14px 16px; border-bottom: 1px solid #e5e7eb;
}
h3 { font-size: 1rem; font-weight: 600; }
.upload-btn {
  padding: 7px 14px; background: #3b82f6; color: white;
  border-radius: 8px; cursor: pointer; font-size: 0.85rem;
}
table { width: 100%; border-collapse: collapse; font-size: 0.88rem; }
th { padding: 10px 14px; text-align: left; color: #6b7280; background: #f9fafb; font-weight: 500; }
td { padding: 10px 14px; border-top: 1px solid #f3f4f6; }
.status-done { color: #22c55e; font-weight: 500; }
.status-processing { color: #f59e0b; }
.status-failed { color: #ef4444; }
.status-pending { color: #9ca3af; }
.doc-error { color: #ef4444; font-size: 0.8rem; max-width: 600px; overflow-wrap: anywhere; margin: 6px 14px; }
.del-doc { color: #ef4444; background: none; border: none; cursor: pointer; font-size: 0.82rem; }
.modal-overlay {
  position: fixed; inset: 0; background: rgba(0,0,0,0.4);
  display: flex; align-items: center; justify-content: center; z-index: 100;
}
.modal {
  background: white; border-radius: 12px; padding: 28px; width: 360px;
  display: flex; flex-direction: column; gap: 10px;
}
.modal h3 { font-size: 1.05rem; font-weight: 600; margin-bottom: 4px; }
.modal label { font-size: 0.85rem; color: #374151; font-weight: 500; }
.modal input {
  padding: 8px 12px; border: 1px solid #d1d5db; border-radius: 8px; font-size: 0.9rem;
}
.modal-actions { display: flex; gap: 8px; justify-content: flex-end; margin-top: 8px; }
.modal-actions button {
  padding: 7px 16px; border-radius: 8px; border: 1px solid #d1d5db;
  background: white; cursor: pointer; font-size: 0.88rem;
}
.modal-actions button.primary { background: #3b82f6; color: white; border-color: #3b82f6; }
</style>
