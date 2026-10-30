<template>
  <div class="chat-layout">
    <ChatSidebar />
    <div class="chat-main">
      <div class="chat-toolbar">
        <ModelSelector />
      </div>
      <div class="messages" ref="messagesEl">
        <div v-if="!session || session.messages.length === 0" class="empty-state">
          <p>👋 开始一个新对话</p>
          <p class="hint">选择检索范围后输入问题</p>
        </div>
        <MessageBubble v-for="msg in session?.messages" :key="msg.id" :msg="msg" />
        <div v-if="isStreaming" class="typing">
          <span class="dot" />
          <span class="stream-status">{{ streamStatus }}</span>
          <span class="dot" />
          <span class="dot" />
        </div>
      </div>
      <ChatInput :isStreaming="isStreaming" @send="handleSend" @stop="handleStop" />
    </div>
  </div>
</template>

<script setup>
import { ref, computed, nextTick, watch, onMounted, onUnmounted } from 'vue'
import { useChatStore } from '@/stores/chat.js'
import { useAuthStore } from '@/stores/auth.js'
import { useConfigStore } from '@/stores/config.js'
import { SSEClient } from '@/utils/sse.js'
import { CHAT_STREAM_URL } from '@/api/chat.js'
import ChatSidebar from '@/components/ChatSidebar.vue'
import MessageBubble from '@/components/MessageBubble.vue'
import ChatInput from '@/components/ChatInput.vue'
import ModelSelector from '@/components/ModelSelector.vue'

const chat = useChatStore()
const auth = useAuthStore()
const config = useConfigStore()
const messagesEl = ref(null)
const isStreaming = ref(false)
const streamStatus = ref('')
let sseClient = null

const session = computed(() => chat.currentSession())

onMounted(() => {
  if (!chat.currentSessionId) chat.newSession()
})

onUnmounted(handleStop)

watch(() => session.value?.messages.map(m => m.content).join(''), () => {
  nextTick(() => {
    if (messagesEl.value) {
      messagesEl.value.scrollTop = messagesEl.value.scrollHeight
    }
  })
})

async function handleSend(payload) {
  if (isStreaming.value) return
  if (!chat.currentSessionId) chat.newSession()

  chat.addMessage({ role: 'user', content: payload.query })

  const msgId = chat.addMessage({ role: 'assistant', content: '' })
  isStreaming.value = true
  streamStatus.value = '正在连接服务'

  const client = new SSEClient(
    CHAT_STREAM_URL,
    {
      session_id: chat.currentSessionId,
      query: payload.query,
      scope_vector: payload.scope_vector,
      scope_database: payload.scope_database,
      scope_web: payload.scope_web,
      knowledge_ids: payload.knowledge_ids,
      db_connection_ids: payload.db_connection_ids,
      llm_config_id: config.selectedConfigId || null,
    },
    auth.token
  )

  sseClient = client
  const active = () => sseClient === client
  client
    .on('status', status => { if (active()) streamStatus.value = status })
    .on('warning', warning => { if (active()) chat.appendToken(msgId, `⚠️ ${warning}\n\n`) })
    .on('source', (src) => { if (active()) chat.addSource(msgId, src) })
    .on('token', (tok) => { if (active()) chat.appendToken(msgId, tok) })
    .on('done', () => { if (active()) isStreaming.value = false })
    .on('error', (err) => {
      if (!active()) return
      chat.appendToken(msgId, `\n\n⚠️ 错误：${err}`)
      isStreaming.value = false
    })

  try {
    await client.start()
  } finally {
    if (active()) {
      sseClient = null
      isStreaming.value = false
      streamStatus.value = ''
    }
  }
}

function handleStop() {
  sseClient?.abort()
  sseClient = null
  isStreaming.value = false
  streamStatus.value = ''
}
</script>

<style scoped>
.chat-layout { display: flex; height: 100vh; overflow: hidden; }
.chat-main { flex: 1; display: flex; flex-direction: column; min-width: 0; }
.chat-toolbar {
  padding: 8px 16px; border-bottom: 1px solid var(--border);
  background: white; display: flex; align-items: center;
}
.messages {
  flex: 1; overflow-y: auto; padding: 24px 20px;
  background: #f9fafb; display: flex; flex-direction: column;
}
.empty-state {
  flex: 1; display: flex; flex-direction: column; align-items: center;
  justify-content: center; color: #9ca3af; gap: 8px;
}
.empty-state p { font-size: 1.1rem; }
.empty-state .hint { font-size: 0.85rem; }
.typing { display: flex; gap: 5px; padding: 10px 0; }
.stream-status { margin-left: 8px; font-size: .85rem; color: #6b7280; }
.dot {
  width: 8px; height: 8px; background: #9ca3af;
  border-radius: 50%; animation: bounce 1.2s infinite;
}
.dot:nth-child(2) { animation-delay: 0.2s; }
.dot:nth-child(3) { animation-delay: 0.4s; }
@keyframes bounce {
  0%, 80%, 100% { transform: translateY(0); }
  40% { transform: translateY(-8px); }
}
</style>
