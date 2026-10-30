<template>
  <aside class="sidebar">
    <div class="sidebar-header">
      <button @click="newChat">新建对话</button>
    </div>
    <div class="session-list">
      <div
        v-for="session in chatStore.sessions"
        :key="session.id"
        :class="{ active: session.id === chatStore.currentSessionId }"
        @click="chatStore.selectSession(session.id)"
        class="session-item"
      >
        <span>{{ formatDate(session.createdAt) }}</span>
      </div>
    </div>
  </aside>
</template>

<script setup>
import { useChatStore } from '@/stores/chat'
import { useRouter } from 'vue-router'

const chatStore = useChatStore()
const router = useRouter()

function newChat() {
  chatStore.newSession()
  router.push('/')
}

function formatDate(dateStr) {
  const d = new Date(dateStr)
  return `${d.getMonth()+1}/${d.getDate()} ${d.getHours()}:${String(d.getMinutes()).padStart(2,'0')}`
}
</script>

<style scoped>
.sidebar {
  width: 240px;
  background: #2c3e50;
  color: white;
  display: flex;
  flex-direction: column;
}

.sidebar-header {
  padding: 16px;
  border-bottom: 1px solid #34495e;
}

.sidebar-header button {
  width: 100%;
  padding: 10px;
  background: #3498db;
  color: white;
  border: none;
  border-radius: 4px;
  cursor: pointer;
}

.session-list {
  flex: 1;
  overflow-y: auto;
}

.session-item {
  padding: 12px 16px;
  cursor: pointer;
  border-bottom: 1px solid #34495e;
  font-size: 14px;
}

.session-item:hover {
  background: #34495e;
}

.session-item.active {
  background: #3498db;
}
</style>