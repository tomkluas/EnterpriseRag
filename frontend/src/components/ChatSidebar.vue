<template>
  <aside class="sidebar">
    <div class="sidebar-header">
      <span class="logo">💬 RAG 系统</span>
    </div>
    <div class="new-chat">
      <button @click="handleNew">＋ 新对话</button>
    </div>
    <div class="session-list">
      <div
        v-for="s in chat.sessions"
        :key="s.id"
        class="session-item"
        :class="{ active: s.id === chat.currentSessionId }"
        @click="chat.switchSession(s.id)"
      >
        <span class="session-title">{{ getTitle(s) }}</span>
        <button class="del-btn" @click.stop="chat.deleteSession(s.id)">×</button>
      </div>
    </div>
    <div class="sidebar-footer">
      <nav>
        <router-link to="/">💬 对话</router-link>
        <router-link to="/knowledge">📚 知识库</router-link>
        <router-link v-if="auth.isAdmin" to="/admin">⚙️ 管理</router-link>
      </nav>
      <div class="user-info">
        <span>{{ auth.user?.username }}</span>
        <button @click="handleLogout">退出</button>
      </div>
    </div>
  </aside>
</template>

<script setup>
import { useChatStore } from '@/stores/chat.js'
import { useAuthStore } from '@/stores/auth.js'
import { useRouter } from 'vue-router'

const chat = useChatStore()
const auth = useAuthStore()
const router = useRouter()

function handleNew() {
  chat.newSession()
}

function getTitle(session) {
  const first = session.messages.find(m => m.role === 'user')
  return first ? first.content.slice(0, 24) : '新对话'
}

function handleLogout() {
  auth.logout()
  router.push('/login')
}
</script>

<style scoped>
.sidebar {
  width: var(--sidebar-w);
  min-height: 100vh;
  background: #1e1e2e;
  color: #cdd6f4;
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
}
.sidebar-header {
  padding: 18px 16px 10px;
  font-weight: 700;
  font-size: 1rem;
  border-bottom: 1px solid #313244;
}
.logo { color: #89b4fa; }
.new-chat { padding: 12px 12px 8px; }
.new-chat button {
  width: 100%; padding: 8px; background: #313244;
  border: 1px solid #45475a; border-radius: 8px;
  color: #cdd6f4; cursor: pointer; font-size: 0.9rem;
}
.new-chat button:hover { background: #45475a; }
.session-list { flex: 1; overflow-y: auto; padding: 4px 8px; }
.session-item {
  display: flex; align-items: center; justify-content: space-between;
  padding: 8px 10px; border-radius: 6px; cursor: pointer; margin-bottom: 2px;
  font-size: 0.85rem; color: #a6adc8;
}
.session-item:hover { background: #313244; }
.session-item.active { background: #45475a; color: #cdd6f4; }
.session-title { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.del-btn {
  background: none; border: none; color: #6c7086;
  cursor: pointer; font-size: 1rem; padding: 0 4px; line-height: 1;
}
.del-btn:hover { color: #f38ba8; }
.sidebar-footer { padding: 12px; border-top: 1px solid #313244; }
nav { display: flex; flex-direction: column; gap: 4px; margin-bottom: 12px; }
nav a {
  display: block; padding: 7px 10px; border-radius: 6px;
  text-decoration: none; color: #a6adc8; font-size: 0.88rem;
}
nav a:hover, nav a.router-link-active { background: #313244; color: #cdd6f4; }
.user-info {
  display: flex; align-items: center; justify-content: space-between;
  font-size: 0.82rem; color: #6c7086;
}
.user-info button {
  background: none; border: none; color: #f38ba8; cursor: pointer; font-size: 0.82rem;
}
</style>
