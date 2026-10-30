<template>
  <div class="setup-page">
    <div class="setup-card">
      <div class="setup-header">
        <h1>企业RAG系统 — 初始化设置</h1>
        <div class="steps-indicator">
          <span v-for="s in 4" :key="s" :class="['dot', { active: s === step, done: s < step }]" />
        </div>
      </div>

      <!-- 步骤1：管理员密码 -->
      <div v-if="step === 1" class="step-body">
        <h2>步骤 1 / 4 · 设置管理员密码</h2>
        <p class="hint">这是您登录系统的密码，请妥善保管。</p>
        <div class="field">
          <label>密码（至少8位）</label>
          <input v-model="adminForm.password" type="password" placeholder="请输入密码" />
        </div>
        <div class="field">
          <label>确认密码</label>
          <input v-model="adminForm.confirm" type="password" placeholder="请再输入一次" />
        </div>
        <p v-if="error" class="error">{{ error }}</p>
        <button class="primary" :disabled="loading" @click="submitAdmin">
          {{ loading ? '提交中...' : '下一步' }}
        </button>
      </div>

      <!-- 步骤2：大模型服务商 -->
      <div v-if="step === 2" class="step-body">
        <h2>步骤 2 / 4 · 选择大模型服务商</h2>
        <p class="hint">选择一个国内大模型服务商，填写 API Key 即可使用对话功能。</p>
        <div class="provider-grid">
          <div
            v-for="p in llmProviders"
            :key="p.key"
            class="provider-card"
            :class="{ selected: llmForm.provider === p.key }"
            @click="llmForm.provider = p.key"
          >
            <div class="provider-name">{{ p.name }}</div>
            <div class="provider-desc">{{ p.desc }}</div>
            <a :href="p.url" target="_blank" rel="noopener" @click.stop>如何获取 API Key →</a>
          </div>
        </div>
        <div class="field">
          <label>API Key</label>
          <input v-model="llmForm.api_key" type="password" placeholder="粘贴您的 API Key" />
        </div>
        <p v-if="error" class="error">{{ error }}</p>
        <p v-if="testResult" class="success">{{ testResult }}</p>
        <div class="btn-row">
          <button class="secondary" :disabled="loading || !llmForm.provider || !llmForm.api_key" @click="testLLM">
            测试连接
          </button>
          <button class="primary" :disabled="loading || !llmForm.provider || !llmForm.api_key" @click="submitLLM">
            {{ loading ? '提交中...' : '下一步' }}
          </button>
        </div>
      </div>

      <!-- 步骤3：Embedding 配置 -->
      <div v-if="step === 3" class="step-body">
        <h2>步骤 3 / 4 · 配置 Embedding（必填）</h2>
        <p class="hint">
          知识库的文档检索功能依赖 Embedding 服务。
          <span v-if="canReuseLLMKey">检测到您选择的服务商支持 Embedding，可直接复用上一步的 Key。</span>
        </p>
        <div class="provider-grid">
          <div
            v-for="p in embeddingProviders"
            :key="p.key"
            class="provider-card"
            :class="{ selected: embeddingForm.provider === p.key }"
            @click="embeddingForm.provider = p.key"
          >
            <div class="provider-name">{{ p.name }}</div>
            <div class="provider-desc">{{ p.desc }}</div>
          </div>
        </div>
        <div class="field">
          <label>API Key</label>
          <input v-model="embeddingForm.api_key" type="password" placeholder="粘贴您的 API Key" />
          <button
            v-if="canReuseLLMKey"
            class="link-btn"
            @click="embeddingForm.api_key = llmForm.api_key"
          >
            使用与大模型相同的 Key
          </button>
        </div>
        <p v-if="error" class="error">{{ error }}</p>
        <button class="primary" :disabled="loading || !embeddingForm.provider || !embeddingForm.api_key" @click="submitEmbedding">
          {{ loading ? '提交中...' : '下一步' }}
        </button>
      </div>

      <!-- 步骤4：联网搜索（可选） -->
      <div v-if="step === 4" class="step-body">
        <h2>步骤 4 / 4 · 联网搜索（可选）</h2>
        <p class="hint">配置后，对话时可勾选"联网搜索"获取实时信息。不需要可直接跳过。</p>
        <div class="provider-grid">
          <div
            class="provider-card"
            :class="{ selected: searchForm.provider === 'bocha' }"
            @click="searchForm.provider = 'bocha'"
          >
            <div class="provider-name">博查</div>
            <div class="provider-desc">国内服务，推荐</div>
            <a href="https://open.bochaai.com" target="_blank" rel="noopener" @click.stop>如何获取 API Key →</a>
          </div>
        </div>
        <div class="field">
          <label>API Key（留空则跳过此步骤）</label>
          <input v-model="searchForm.api_key" type="password" placeholder="粘贴您的 API Key（可选）" />
        </div>
        <p v-if="error" class="error">{{ error }}</p>
        <div class="btn-row">
          <button class="secondary" :disabled="loading" @click="skipSearch">跳过</button>
          <button class="primary" :disabled="loading || !searchForm.api_key" @click="submitSearch">
            {{ loading ? '提交中...' : '完成设置' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { setupApi } from '@/api/setup.js'
import { clearInitStatusCache } from '@/router/index.js'

const router = useRouter()
const step = ref(1)
const loading = ref(false)
const error = ref('')
const testResult = ref('')

const adminForm = ref({ password: '', confirm: '' })
const llmForm = ref({ provider: '', api_key: '' })
const embeddingForm = ref({ provider: '', api_key: '' })
const searchForm = ref({ provider: 'bocha', api_key: '' })

const llmProviders = [
  { key: 'zhipu', name: '智谱AI', desc: '推荐，注册即送额度', url: 'https://open.bigmodel.cn' },
  { key: 'qwen', name: '通义千问', desc: '阿里云，长文本处理强', url: 'https://dashscope.aliyun.com' },
  { key: 'deepseek', name: 'DeepSeek', desc: '复杂推理、代码问题', url: 'https://platform.deepseek.com' },
  { key: 'doubao', name: '豆包', desc: '字节跳动，性价比极高', url: 'https://www.volcengine.com/product/doubao' },
  { key: 'moonshot', name: '月之暗面', desc: 'Kimi 背后的模型', url: 'https://platform.moonshot.cn' },
]

const embeddingProviders = [
  { key: 'zhipu', name: '智谱AI', desc: 'embedding-3 模型' },
  { key: 'qwen', name: '通义千问', desc: 'text-embedding-v3 模型' },
]

// 智谱/通义同时提供 LLM 和 Embedding，选择这两个服务商时可一键复用 Key
const canReuseLLMKey = computed(() =>
  embeddingForm.value.provider && embeddingForm.value.provider === llmForm.value.provider
)

onMounted(async () => {
  try {
    const status = await setupApi.getStatus()
    if (status.initialized) {
      router.replace('/login')
    }
  } catch {}
})

async function submitAdmin() {
  error.value = ''
  if (adminForm.value.password.length < 8) {
    error.value = '密码长度至少8位'
    return
  }
  if (adminForm.value.password !== adminForm.value.confirm) {
    error.value = '两次输入的密码不一致'
    return
  }
  loading.value = true
  try {
    await setupApi.setAdmin(adminForm.value.password)
    step.value = 2
  } catch (e) {
    error.value = e.response?.data?.detail || '提交失败，请重试'
  } finally {
    loading.value = false
  }
}

async function testLLM() {
  error.value = ''
  testResult.value = ''
  loading.value = true
  try {
    const res = await setupApi.testLLM(llmForm.value.provider, llmForm.value.api_key)
    testResult.value = `连接成功：${res.response}`
  } catch (e) {
    error.value = e.response?.data?.detail || '连接失败，请检查 API Key 是否正确'
  } finally {
    loading.value = false
  }
}

async function submitLLM() {
  error.value = ''
  loading.value = true
  try {
    await setupApi.setLLM(llmForm.value.provider, llmForm.value.api_key)
    // 默认预选相同服务商作为 Embedding（如果支持）
    if (embeddingProviders.some(p => p.key === llmForm.value.provider)) {
      embeddingForm.value.provider = llmForm.value.provider
    }
    step.value = 3
  } catch (e) {
    error.value = e.response?.data?.detail || '提交失败，请重试'
  } finally {
    loading.value = false
  }
}

async function submitEmbedding() {
  error.value = ''
  loading.value = true
  try {
    await setupApi.setEmbedding(embeddingForm.value.provider, embeddingForm.value.api_key)
    step.value = 4
  } catch (e) {
    error.value = e.response?.data?.detail || '提交失败，请重试'
  } finally {
    loading.value = false
  }
}

async function submitSearch() {
  error.value = ''
  loading.value = true
  try {
    await setupApi.setSearch(searchForm.value.provider, searchForm.value.api_key)
    await finishSetup()
  } catch (e) {
    error.value = e.response?.data?.detail || '提交失败，请重试'
    loading.value = false
  }
}

async function skipSearch() {
  await finishSetup()
}

async function finishSetup() {
  loading.value = true
  try {
    await setupApi.complete()
    clearInitStatusCache()
    router.replace('/login')
  } catch (e) {
    error.value = '完成设置失败，请重试'
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.setup-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  padding: 20px;
}
.setup-card {
  background: white;
  border-radius: 14px;
  padding: 36px 40px;
  width: 560px;
  max-width: 100%;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.2);
}
.setup-header h1 {
  font-size: 1.3rem;
  font-weight: 700;
  margin-bottom: 16px;
  color: #111;
}
.steps-indicator { display: flex; gap: 8px; margin-bottom: 28px; }
.dot { width: 32px; height: 4px; border-radius: 2px; background: #e5e7eb; }
.dot.active { background: #3b82f6; }
.dot.done { background: #93c5fd; }
.step-body h2 { font-size: 1.05rem; font-weight: 600; margin-bottom: 6px; color: #111; }
.hint { font-size: 0.85rem; color: #6b7280; margin-bottom: 18px; line-height: 1.5; }
.field { margin-bottom: 14px; }
label { display: block; font-size: 0.85rem; font-weight: 500; margin-bottom: 6px; color: #374151; }
input {
  width: 100%; padding: 10px 12px; border: 1px solid #d1d5db;
  border-radius: 8px; font-size: 0.95rem; outline: none;
}
input:focus { border-color: #3b82f6; }
.link-btn {
  margin-top: 6px; background: none; border: none; color: #3b82f6;
  font-size: 0.8rem; cursor: pointer; padding: 0;
}
.error { color: #ef4444; font-size: 0.85rem; margin: 8px 0; }
.success { color: #22c55e; font-size: 0.85rem; margin: 8px 0; }
.provider-grid {
  display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; margin-bottom: 16px;
}
.provider-card {
  border: 1px solid #e5e7eb; border-radius: 10px; padding: 12px 14px;
  cursor: pointer; transition: all 0.15s;
}
.provider-card:hover { border-color: #93c5fd; }
.provider-card.selected { border-color: #3b82f6; background: #eff6ff; }
.provider-name { font-weight: 600; font-size: 0.92rem; color: #111; margin-bottom: 2px; }
.provider-desc { font-size: 0.78rem; color: #9ca3af; margin-bottom: 6px; }
.provider-card a { font-size: 0.78rem; color: #3b82f6; text-decoration: none; }
.provider-card a:hover { text-decoration: underline; }
.btn-row { display: flex; gap: 10px; margin-top: 8px; }
button {
  padding: 10px 22px; border-radius: 8px; font-size: 0.92rem; font-weight: 500;
  cursor: pointer; border: none; transition: background 0.15s;
}
button.primary { background: #3b82f6; color: white; flex: 1; }
button.primary:hover:not(:disabled) { background: #2563eb; }
button.primary:disabled { opacity: 0.5; cursor: not-allowed; }
button.secondary { background: white; color: #374151; border: 1px solid #d1d5db; }
button.secondary:hover:not(:disabled) { background: #f9fafb; }
</style>
