import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { login as apiLogin, getMe as apiGetMe } from '@/api/auth'
import { useChatStore } from '@/stores/chat'

export const useAuthStore = defineStore('auth', () => {
  const token = ref(localStorage.getItem('token') || '')
  const userId = ref(localStorage.getItem('user_id') || '')
  const username = ref(localStorage.getItem('username') || '')
  const role = ref(localStorage.getItem('role') || '')

  const isLoggedIn = computed(() => !!token.value)
  const isAdmin = computed(() => role.value === 'admin')

  async function login(user, pass) {
    const data = await apiLogin(user, pass)
    token.value = data.access_token
    userId.value = data.user_id
    username.value = data.username
    role.value = data.role

    localStorage.setItem('token', data.access_token)
    localStorage.setItem('user_id', data.user_id)
    localStorage.setItem('username', data.username)
    localStorage.setItem('role', data.role)

    return data
  }

  async function fetchMe() {
    if (!token.value) return null
    try {
      const data = await apiGetMe(token.value)
      userId.value = data.user_id
      username.value = data.username
      role.value = data.role
      return data
    } catch {
      logout()
      return null
    }
  }

  function logout() {
    useChatStore().clearSessions()
    token.value = ''
    userId.value = ''
    username.value = ''
    role.value = ''
    localStorage.removeItem('token')
    localStorage.removeItem('user_id')
    localStorage.removeItem('username')
    localStorage.removeItem('role')
  }

  return {
    token,
    userId,
    username,
    role,
    isLoggedIn,
    isAdmin,
    login,
    fetchMe,
    logout,
  }
})
