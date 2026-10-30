<template>
  <div class="header-actions">
    <nav aria-label="主导航">
      <RouterLink to="/" exact-active-class="active">对话</RouterLink>
      <RouterLink to="/knowledge" active-class="active">知识库</RouterLink>
      <RouterLink v-if="authStore.isAdmin" to="/admin" active-class="active">系统设置</RouterLink>
    </nav>
    <button type="button" @click="logout">退出</button>
  </div>
</template>

<script setup>
import { RouterLink, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const authStore = useAuthStore()
const router = useRouter()

function logout() {
  authStore.logout()
  router.push('/login')
}
</script>

<style scoped>
.header-actions, nav {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}

nav a {
  padding: 8px 12px;
  border-radius: 4px;
  color: #34495e;
  text-decoration: none;
  font-size: 14px;
}

nav a:hover, nav a.active {
  background: #eaf4fc;
  color: #1675b5;
}

button {
  margin-left: 8px;
  padding: 8px 16px;
  background: #f5f5f5;
  border: 1px solid #ddd;
  border-radius: 4px;
  cursor: pointer;
}
</style>
