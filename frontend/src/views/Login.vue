<template>
  <div class="login-page">
    <div class="login-card">
      <h1>企业RAG系统</h1>
      <p class="subtitle">智能知识问答平台</p>
      <form @submit.prevent="handleLogin">
        <div class="field">
          <label>用户名</label>
          <input v-model="username" type="text" placeholder="请输入用户名" required autofocus />
        </div>
        <div class="field">
          <label>密码</label>
          <input v-model="password" type="password" placeholder="请输入密码" required />
        </div>
        <p v-if="error" class="error">{{ error }}</p>
        <button type="submit" :disabled="loading">
          {{ loading ? '登录中...' : '登录' }}
        </button>
      </form>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth.js'
import { authApi } from '@/api/auth.js'

const router = useRouter()
const auth = useAuthStore()
const username = ref('')
const password = ref('')
const error = ref('')
const loading = ref(false)

async function handleLogin() {
  error.value = ''
  loading.value = true
  try {
    const data = await authApi.login(username.value, password.value)
    auth.setAuth(data)
    router.push('/')
  } catch (e) {
    error.value = e.response?.data?.detail || '登录失败，请检查用户名和密码'
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
}
.login-card {
  background: white;
  border-radius: 12px;
  padding: 40px;
  width: 380px;
  box-shadow: 0 20px 60px rgba(0,0,0,0.2);
}
h1 { font-size: 1.6rem; font-weight: 700; color: #111; margin-bottom: 4px; }
.subtitle { color: #6b7280; margin-bottom: 28px; font-size: 0.9rem; }
.field { margin-bottom: 16px; }
label { display: block; font-size: 0.85rem; font-weight: 500; margin-bottom: 6px; color: #374151; }
input {
  width: 100%; padding: 10px 12px; border: 1px solid #d1d5db;
  border-radius: 8px; font-size: 0.95rem; outline: none; transition: border 0.2s;
}
input:focus { border-color: #3b82f6; }
.error { color: #ef4444; font-size: 0.85rem; margin-bottom: 12px; }
button {
  width: 100%; padding: 11px; background: #3b82f6; color: white;
  border: none; border-radius: 8px; font-size: 1rem; font-weight: 500;
  cursor: pointer; transition: background 0.2s; margin-top: 8px;
}
button:hover:not(:disabled) { background: #2563eb; }
button:disabled { opacity: 0.6; cursor: not-allowed; }
</style>
