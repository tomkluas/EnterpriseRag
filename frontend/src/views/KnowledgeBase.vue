<template>
  <div class="kb-layout">
    <header class="kb-header">
      <h1>知识库管理</h1>
      <AppNavigation />
    </header>

    <div class="kb-content">
      <p v-if="error" role="alert" class="request-error">{{ error }}</p>
      <div class="kb-actions">
        <button :disabled="busy" @click="showCreate = true">创建知识库</button>
      </div>

      <div v-if="showCreate" class="modal">
        <div class="modal-content">
          <h3>创建知识库</h3>
          <input v-model="newKbName" placeholder="知识库名称" />
          <input v-model="newKbDesc" placeholder="描述" />
          <div class="modal-actions">
            <button :disabled="busy" @click="createKb">创建</button>
            <button :disabled="busy" @click="showCreate = false">取消</button>
          </div>
        </div>
      </div>

      <div class="kb-list">
        <div v-for="kb in kbs" :key="kb.id" class="kb-card">
          <div class="kb-info">
            <h3>{{ kb.name }}</h3>
            <p>{{ kb.description }}</p>
            <span>文档数: {{ kb.doc_count }}</span>
          </div>
          <div class="kb-actions">
            <button :disabled="busy" @click="selectKb(kb)">上传文档</button>
            <button :disabled="busy" @click="viewDocs(kb)">查看文档</button>
            <button :disabled="busy" v-if="authStore.isAdmin" @click="deleteKb(kb.id)">删除</button>
          </div>
        </div>
      </div>

      <div v-if="selectedKb" class="modal">
        <div class="modal-content">
          <h3>上传文档 - {{ selectedKb.name }}</h3>
          <input type="file" @change="handleFileUpload" accept=".pdf,.docx,.xlsx,.txt,.md" />
          <div class="modal-actions">
            <button @click="uploadDoc" :disabled="!file || busy">{{ busy ? '上传中...' : '上传' }}</button>
            <button :disabled="busy" @click="selectedKb = null">取消</button>
          </div>
        </div>
      </div>

      <div v-if="showDocs" class="modal">
        <div class="modal-content docs-modal">
          <h3>文档列表 - {{ showDocs.name }}</h3>
          <div v-for="doc in documents" :key="doc.id" class="doc-item">
            <span>{{ doc.filename }}</span>
            <span :class="'status-' + doc.status">{{ doc.status }}</span>
            <span v-if="doc.error_msg" role="alert">{{ doc.error_msg }}</span>
            <button :disabled="busy" @click="deleteDoc(showDocs.id, doc.id)">删除</button>
          </div>
          <div class="modal-actions">
            <button :disabled="busy" @click="viewDocs(showDocs)">刷新状态</button>
            <button :disabled="busy" @click="showDocs = null">关闭</button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { errorMessage } from '@/utils/errors'
import AppNavigation from '@/components/AppNavigation.vue'
import { useAuthStore } from '@/stores/auth'
import { listKnowledgeBases, createKnowledgeBase, uploadDocument, listDocuments, deleteDocument, deleteKnowledgeBase } from '@/api/knowledge'

const busy = ref(false)
const error = ref('')
async function run(action) {
  if (busy.value) return
  busy.value = true
  error.value = ''
  try { await action() } catch (e) { error.value = errorMessage(e) } finally { busy.value = false }
}

const authStore = useAuthStore()
const kbs = ref([])
const showCreate = ref(false)
const newKbName = ref('')
const newKbDesc = ref('')
const selectedKb = ref(null)
const file = ref(null)
const showDocs = ref(null)
const documents = ref([])

async function loadKbs() {
  kbs.value = await listKnowledgeBases()
}

async function createKb() {
  await run(async () => {
    if (!newKbName.value.trim()) throw new Error('请填写知识库名称')
    await createKnowledgeBase(newKbName.value.trim(), newKbDesc.value)
    showCreate.value = false
    newKbName.value = ''
    newKbDesc.value = ''
    await loadKbs()
  })
}

function selectKb(kb) {
  selectedKb.value = kb
  file.value = null
}

function handleFileUpload(e) {
  file.value = e.target.files[0]
}

async function uploadDoc() {
  await run(async () => {
    if (!file.value || !selectedKb.value) return
    await uploadDocument(selectedKb.value.id, file.value)
    selectedKb.value = null
    file.value = null
    await loadKbs()
  })
}

async function viewDocs(kb) {
  await run(async () => {
    const loaded = await listDocuments(kb.id)
    documents.value = loaded
    showDocs.value = kb
  })
}

async function deleteDoc(kbId, docId) {
  await run(async () => {
    await deleteDocument(kbId, docId)
    if (showDocs.value) {
      documents.value = await listDocuments(showDocs.value.id)
    }
    await loadKbs()
  })
}

async function deleteKb(kbId) {
  await run(async () => {
    await deleteKnowledgeBase(kbId)
    await loadKbs()
  })
}

onMounted(() => run(loadKbs))
</script>

<style scoped>
.request-error { color: #c0392b; padding: 12px 20px; position: relative; z-index: 101; background: white; }
button:disabled { opacity: 0.6; cursor: wait; }
.kb-layout {
  min-height: 100vh;
}

.kb-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px 24px;
  background: white;
  border-bottom: 1px solid #eee;
}

.modal {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0,0,0,0.5);
  display: flex;
  justify-content: center;
  align-items: center;
  z-index: 100;
}

.modal-content {
  background: white;
  padding: 24px;
  border-radius: 8px;
  width: 400px;
  max-width: 90%;
}

.modal-content input {
  width: 100%;
  padding: 10px;
  margin: 10px 0;
  border: 1px solid #ddd;
  border-radius: 4px;
}

.modal-actions {
  display: flex;
  gap: 10px;
  margin-top: 15px;
}

.modal-actions button {
  flex: 1;
  padding: 10px;
  border: 1px solid #ddd;
  border-radius: 4px;
  cursor: pointer;
}

.kb-list {
  padding: 20px;
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 20px;
}

.kb-card {
  background: white;
  padding: 20px;
  border-radius: 8px;
  box-shadow: 0 2px 8px rgba(0,0,0,0.1);
}

.kb-info h3 {
  margin-bottom: 10px;
}

.kb-info p {
  color: #666;
  font-size: 14px;
  margin-bottom: 10px;
}

.kb-info span {
  font-size: 12px;
  color: #999;
}

.kb-actions {
  display: flex;
  gap: 10px;
  margin-top: 15px;
}

.kb-actions button {
  padding: 8px 16px;
  border: 1px solid #ddd;
  border-radius: 4px;
  cursor: pointer;
  background: #f5f5f5;
}

.doc-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px;
  border-bottom: 1px solid #eee;
}

.status-done { color: #2ecc71; }
.status-failed { color: #e74c3c; }
.status-pending, .status-processing { color: #f39c12; }
</style>
