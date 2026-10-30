<template>
  <div class="chat-input-container">
    <div class="scope-selector">
      <label>
        <input type="checkbox" v-model="scopeVector" />
        <span>📚 知识库</span>
      </label>
      <label>
        <input type="checkbox" v-model="scopeDatabase" />
        <span>🗄️ 数据库</span>
      </label>
      <label>
        <input type="checkbox" v-model="scopeWeb" />
        <span>🌐 联网搜索</span>
      </label>
    </div>

    <div class="input-row">
      <textarea
        v-model="inputText"
        @keydown.enter.exact.prevent="sendMessage"
        placeholder="输入问题，Enter发送，Shift+Enter换行"
        :disabled="isStreaming"
      ></textarea>
      <button v-if="!isStreaming" @click="sendMessage">发送</button>
      <button v-else @click="stopStreaming" class="stop-btn">停止</button>
    </div>
  </div>
</template>

<script setup>
import { ref, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import { useChatStore } from '@/stores/chat'
import { useAuthStore } from '@/stores/auth'
import { SSEClient } from '@/utils/sse'

const chatStore = useChatStore()
const authStore = useAuthStore()
const router = useRouter()

const inputText = ref('')
const isStreaming = ref(false)
const scopeVector = ref(true)
const scopeDatabase = ref(false)
const scopeWeb = ref(false)

let sseClient = null

async function sendMessage() {
  if (!inputText.value.trim() || isStreaming.value) return

  const query = inputText.value.trim()
  inputText.value = ''

  if (!chatStore.currentSession()) chatStore.newSession()
  chatStore.addMessage({ role: 'user', content: query })
  const currentMsgId = chatStore.addMessage({ role: 'assistant', content: '' })
  isStreaming.value = true

  const body = {
    session_id: chatStore.currentSessionId,
    query,
    scope_vector: scopeVector.value,
    scope_database: scopeDatabase.value,
    scope_web: scopeWeb.value,
    knowledge_ids: [],
    db_connection_ids: [],
    llm_config_id: null,
  }

  const client = new SSEClient('/api/v1/chat/stream', body, authStore.token)
  sseClient = client

  client
    .on('source', (data) => {
      chatStore.addSource(currentMsgId, data)
    })
    .on('token', (token) => {
      chatStore.appendToken(currentMsgId, token)
    })
    .on('unauthorized', () => {
      authStore.logout()
      router.replace('/login')
    })
    .on('error', (err) => {
      console.error('SSE error:', err)
      chatStore.appendToken(currentMsgId, `\n[错误: ${err}]`)
    })
    .on('close', () => {
      if (sseClient === client) {
        isStreaming.value = false
        sseClient = null
      }
    })

  await client.start()
}

function stopStreaming() {
  if (sseClient) {
    sseClient.abort()
    sseClient = null
    isStreaming.value = false
  }
}

onBeforeUnmount(stopStreaming)
</script>

<style scoped>
.chat-input-container {
  padding: 16px 24px;
  background: white;
  border-top: 1px solid #eee;
}

.scope-selector {
  display: flex;
  gap: 20px;
  margin-bottom: 12px;
}

.scope-selector label {
  display: flex;
  align-items: center;
  gap: 6px;
  cursor: pointer;
  font-size: 14px;
}

.scope-selector input[type="checkbox"] {
  cursor: pointer;
}

.input-row {
  display: flex;
  gap: 12px;
}

.input-row textarea {
  flex: 1;
  padding: 12px;
  border: 1px solid #ddd;
  border-radius: 4px;
  resize: none;
  min-height: 60px;
  font-family: inherit;
  font-size: 14px;
}

.input-row button {
  padding: 12px 24px;
  background: #3498db;
  color: white;
  border: none;
  border-radius: 4px;
  cursor: pointer;
}

.stop-btn {
  background: #e74c3c !important;
}
</style>
