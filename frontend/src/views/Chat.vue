<template>
  <div class="chat-layout">
    <ChatSidebar />
    <main class="chat-main">
      <header class="chat-header">
        <h2>对话</h2>
        <AppNavigation />
      </header>

      <div class="messages-container">
        <div v-if="!chatStore.currentSession()?.messages.length" class="welcome">
          欢迎使用企业RAG系统，请开始对话
        </div>
        <template v-else>
          <MessageBubble
            v-for="msg in chatStore.currentSession()?.messages"
            :key="msg.id"
            :message="msg"
          />
        </template>
      </div>

      <ChatInput />
    </main>
  </div>
</template>

<script setup>
import { onMounted } from 'vue'
import AppNavigation from '@/components/AppNavigation.vue'
import { useChatStore } from '@/stores/chat'
import ChatSidebar from '@/components/ChatSidebar.vue'
import ChatInput from '@/components/ChatInput.vue'
import MessageBubble from '@/components/MessageBubble.vue'

const chatStore = useChatStore()

onMounted(() => {
  if (!chatStore.currentSessionId) {
    chatStore.newSession()
  }
})
</script>

<style scoped>
.chat-layout {
  display: flex;
  height: 100vh;
}

.chat-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  background: #fafafa;
}

.chat-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px 24px;
  background: white;
  border-bottom: 1px solid #eee;
}

.chat-header h2 {
  font-size: 18px;
  color: #333;
}

.chat-header button {
  padding: 8px 16px;
  background: #f5f5f5;
  border: 1px solid #ddd;
  border-radius: 4px;
  cursor: pointer;
}

.messages-container {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
}

.welcome {
  text-align: center;
  color: #999;
  margin-top: 100px;
}
</style>
