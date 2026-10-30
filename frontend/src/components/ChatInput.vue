<template>
  <div class="chat-input-area">
    <div class="scope-bar">
      <label class="scope-item">
        <input type="checkbox" v-model="scopeVector" :disabled="isStreaming" />
        <span>📚 知识库</span>
        <select
          v-if="scopeVector"
          multiple
          v-model="selectedKbs"
          class="sub-select"
          :disabled="isStreaming"
        >
          <option value="">全部</option>
          <option v-for="kb in kbs" :key="kb.id" :value="kb.id">{{ kb.name }}</option>
        </select>
      </label>
      <label class="scope-item">
        <input type="checkbox" v-model="scopeDatabase" :disabled="isStreaming" />
        <span>🗄️ 数据库</span>
        <select
          v-if="scopeDatabase"
          multiple
          v-model="selectedDbs"
          class="sub-select"
          :disabled="isStreaming"
        >
          <option value="">全部</option>
          <option v-for="db in dbs" :key="db.id" :value="db.id">{{ db.name }}</option>
        </select>
      </label>
      <label class="scope-item">
        <input type="checkbox" v-model="scopeWeb" :disabled="isStreaming" />
        <span>🌐 联网搜索<span v-if="searchProviderLabel" class="provider-tag">（{{ searchProviderLabel }}）</span></span>
      </label>
    </div>
    <div class="input-row">
      <textarea
        v-model="text"
        placeholder="输入问题，Enter 发送，Shift+Enter 换行..."
        rows="2"
        :disabled="isStreaming"
        @keydown.enter.exact.prevent="handleSend"
      />
      <button v-if="!isStreaming" class="send-btn" :disabled="!text.trim()" @click="handleSend">
        发送
      </button>
      <button v-else class="stop-btn" @click="emit('stop')">停止</button>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { knowledgeApi } from '@/api/knowledge.js'
import { adminApi } from '@/api/admin.js'
import { useAuthStore } from '@/stores/auth.js'

const props = defineProps({ isStreaming: Boolean })
const emit = defineEmits(['send', 'stop'])

const text = ref('')
const scopeVector = ref(true)
const scopeDatabase = ref(false)
const scopeWeb = ref(false)
const selectedKbs = ref([])
const selectedDbs = ref([])
const kbs = ref([])
const dbs = ref([])
const auth = useAuthStore()
const searchProviderLabel = ref('')

const PROVIDER_LABELS = { tavily: 'Tavily', bocha: '博查' }

onMounted(async () => {
  try { kbs.value = await knowledgeApi.list() } catch {}
  if (auth.isAdmin) {
    try { dbs.value = await adminApi.getDBConnections() } catch {}
  }
  try {
    const info = await adminApi.getSystemInfo()
    searchProviderLabel.value = PROVIDER_LABELS[info.search_provider] || info.search_provider
  } catch {}
})

function handleSend() {
  if (!text.value.trim() || props.isStreaming) return
  emit('send', {
    query: text.value.trim(),
    scope_vector: scopeVector.value,
    scope_database: scopeDatabase.value,
    scope_web: scopeWeb.value,
    knowledge_ids: selectedKbs.value.filter(Boolean),
    db_connection_ids: selectedDbs.value.filter(Boolean),
  })
  text.value = ''
}
</script>

<style scoped>
.chat-input-area {
  border-top: 1px solid var(--border);
  background: white;
  padding: 12px 16px;
}
.scope-bar {
  display: flex; gap: 16px; align-items: center;
  margin-bottom: 10px; flex-wrap: wrap;
}
.scope-item {
  display: flex; align-items: center; gap: 6px;
  font-size: 0.83rem; color: #374151; cursor: pointer;
}
.scope-item input[type="checkbox"] { cursor: pointer; }
.provider-tag { color: #9ca3af; font-size: 0.76rem; }
.sub-select {
  font-size: 0.78rem; padding: 2px 4px;
  border: 1px solid #d1d5db; border-radius: 4px;
  max-height: 80px;
}
.input-row { display: flex; gap: 10px; align-items: flex-end; }
textarea {
  flex: 1; padding: 10px 12px; border: 1px solid #d1d5db; border-radius: 8px;
  font-size: 0.95rem; resize: none; outline: none; font-family: inherit;
  line-height: 1.5; max-height: 160px;
}
textarea:focus { border-color: #3b82f6; }
.send-btn, .stop-btn {
  padding: 10px 20px; border: none; border-radius: 8px;
  font-size: 0.9rem; font-weight: 500; cursor: pointer; white-space: nowrap;
}
.send-btn { background: #3b82f6; color: white; }
.send-btn:hover:not(:disabled) { background: #2563eb; }
.send-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.stop-btn { background: #ef4444; color: white; }
.stop-btn:hover { background: #dc2626; }
</style>
