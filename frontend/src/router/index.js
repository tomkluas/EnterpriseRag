import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '@/stores/auth.js'
import { setupApi } from '@/api/setup.js'
import Setup from '@/views/Setup.vue'
import Login from '@/views/Login.vue'
import Chat from '@/views/Chat.vue'
import KnowledgeBase from '@/views/KnowledgeBase.vue'
import Admin from '@/views/Admin.vue'

const routes = [
  { path: '/setup', component: Setup, meta: { requiresAuth: false } },
  { path: '/login', component: Login, meta: { requiresAuth: false } },
  { path: '/', component: Chat, meta: { requiresAuth: true } },
  { path: '/knowledge', component: KnowledgeBase, meta: { requiresAuth: true } },
  { path: '/admin', component: Admin, meta: { requiresAuth: true, requiresAdmin: true } },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

// 缓存初始化状态，避免每次路由跳转都打一次 /setup/status
let initStatusCache = null

export function clearInitStatusCache() {
  initStatusCache = null
}

async function checkInitialized() {
  if (initStatusCache !== null) return initStatusCache
  try {
    const res = await setupApi.getStatus()
    initStatusCache = res.initialized
  } catch {
    initStatusCache = true   // 接口异常时不阻塞用户，按已初始化处理，避免死循环跳转 /setup
  }
  return initStatusCache
}

router.beforeEach(async (to, from, next) => {
  // /setup 页面本身不需要再检查（否则会死循环：未初始化→跳/setup→检查未初始化→跳/setup）
  if (to.path === '/setup') {
    return next()
  }

  const initialized = await checkInitialized()
  if (!initialized) {
    return next('/setup')
  }

  const auth = useAuthStore()
  if (to.meta.requiresAuth && !auth.token) {
    return next('/login')
  }
  if (to.meta.requiresAdmin && auth.user?.role !== 'admin') {
    return next('/')
  }
  next()
})

export default router
