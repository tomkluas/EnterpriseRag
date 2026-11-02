<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import AppDialog from './AppDialog.vue'
import TraceMessage from './TraceMessage.vue'
import { api } from '../lib/api'

const filters = ref({ query: '', owner: '', state: '', start: '', end: '' })
const result = ref({ items: [], total: 0, summary: {} }), page = ref(1), pageSize = ref(20)
const busy = ref(false), error = ref(''), detail = ref(null), detailBusy = ref(false), detailError = ref(''), opening = ref(''), opener = ref(null)
const pages = computed(() => Math.max(1, Math.ceil(result.value.total / pageSize.value)))
const detailPages = computed(() => Math.max(1, Math.ceil((detail.value?.total || 0) / (detail.value?.page_size || 20))))
const labels = { completed: '已完成', failed: '失败', processing: '处理中' }
const rounds = ref(null)
const time = value => value == null ? '暂无记录' : new Date(value * 1000).toLocaleString('zh-CN')
let disposed = false, listController = null, detailController = null

async function load(reset = false, targetPage = page.value) {
  if (busy.value) return
  busy.value = true; error.value = ''
  listController = new AbortController()
  try {
    const requestedPage = reset ? 1 : targetPage
    const params = new URLSearchParams({ page: String(requestedPage), page_size: String(pageSize.value) })
    // 消息筛选只决定哪些会话入选；列表显示完整会话统计，详情不丢弃未命中的前文。
    for (const [key, value] of Object.entries(filters.value)) if (value) params.set(key, key === 'start' || key === 'end' ? String(new Date(value).getTime() / 1000) : value)
    const data = await api('/admin/trace-conversations?' + params, { signal: listController.signal })
    if (!disposed) { result.value = data; page.value = data.page }
  } catch (e) { if (!disposed && e.name !== 'AbortError') error.value = e.message }
  finally { if (!disposed) busy.value = false }
}

async function inspect(id, targetPage = 1, event = null) {
  if (detailBusy.value) return
  if (event) opener.value = event.currentTarget
  detailBusy.value = true; opening.value = id; detailError.value = ''
  detailController = new AbortController()
  try {
    // 只取当前页的完整问答，长会话不会一次加载全部证据；时间顺序由服务端保证。
    const data = await api(`/admin/trace-conversations/${encodeURIComponent(id)}?page=${targetPage}&page_size=20`, { signal: detailController.signal })
    if (!disposed) {
      detail.value = data
      // 翻页后回到当前页首轮，不能沿用上一页接近底部的滚动位置而漏看新页开头。
      await nextTick()
      rounds.value?.closest('.dialog-body')?.scrollTo({ top: 0 })
    }
  } catch (e) { if (!disposed && e.name !== 'AbortError') detailError.value = e.message }
  finally { if (!disposed) { detailBusy.value = false; opening.value = '' } }
}

function close() { if (!detailBusy.value) { detail.value = null; detailError.value = '' } }
onMounted(() => load())
onUnmounted(() => { disposed = true; listController?.abort(); detailController?.abort() })
</script>

<template>
  <section class="trace-admin content-page" aria-labelledby="trace-admin-title">
    <header><h2 id="trace-admin-title">问答审计</h2><p>按会话查看成员的多轮问答、来源与反馈。查看详情会记录审计日志。</p></header>
    <form class="trace-filters" @submit.prevent="load(true)">
      <label>问题关键词<input v-model="filters.query" placeholder="搜索任意一轮问题" :disabled="busy"></label>
      <label>成员账号<input v-model="filters.owner" placeholder="完整账号" :disabled="busy"></label>
      <label>包含问答状态<select v-model="filters.state" :disabled="busy"><option value="">全部状态</option><option v-for="(label,key) in labels" :key="key" :value="key">{{ label }}</option></select></label>
      <label>消息开始时间<input v-model="filters.start" type="datetime-local" :disabled="busy"></label>
      <label>消息结束时间<input v-model="filters.end" type="datetime-local" :disabled="busy"></label>
      <button class="primary" :disabled="busy">{{ busy ? '正在查询…' : '查询' }}</button>
    </form>
    <p class="trace-filter-note">任一轮问题符合条件，即显示所属会话；打开后可查看完整上下文。</p>
    <p v-if="error" class="error" role="alert">{{ error }}</p><p v-if="detailError && !detail" class="error" role="alert">{{ detailError }}</p>
    <div class="trace-summary" aria-live="polite"><span>筛选结果 <strong>{{ result.summary.conversations || 0 }} 个会话</strong></span><span>共 {{ result.summary.messages || 0 }} 轮问答</span><span>匹配 {{ result.summary.matching_messages || 0 }} 轮</span><span>完成 {{ result.summary.completed || 0 }}</span><span>失败 {{ result.summary.failed || 0 }}</span><span>处理中 {{ result.summary.processing || 0 }}</span><span>有反馈 {{ result.summary.with_feedback || 0 }}</span></div>
    <div class="trace-table-wrap"><table :aria-busy="busy"><thead><tr><th>会话主题</th><th>成员</th><th>问答轮数</th><th>最近活动</th><th>问答情况</th><th>详情</th></tr></thead><tbody>
      <tr v-for="item in result.items" :key="item.id"><td class="trace-topic"><strong>{{ item.title }}</strong><small>开始于 {{ time(item.first_at) }}</small></td><td>{{ item.owner }}</td><td>{{ item.message_count }} 轮<small v-if="item.matching_messages !== item.message_count">其中 {{ item.matching_messages }} 轮匹配</small></td><td>{{ time(item.last_at) }}</td><td class="trace-counts"><span>完成 {{ item.completed }}</span><span v-if="item.failed" class="trace-failed">失败 {{ item.failed }}</span><span v-if="item.processing">处理中 {{ item.processing }}</span><span v-if="item.with_feedback">反馈 {{ item.with_feedback }}</span></td><td><button class="secondary" :disabled="detailBusy || busy" @click="inspect(item.id, 1, $event)">{{ opening === item.id ? '正在加载…' : '查看会话' }}</button></td></tr>
      <tr v-if="!result.items.length"><td colspan="6" class="empty-row">{{ busy ? '正在加载会话…' : '没有符合条件的问答会话。' }}</td></tr>
    </tbody></table></div>
    <div class="trace-pagination"><span>共 {{ result.total }} 个会话，第 {{ page }} / {{ pages }} 页</span><label>每页 <select v-model.number="pageSize" :disabled="busy" @change="load(true)"><option :value="20">20 个会话</option><option :value="50">50 个会话</option><option :value="100">100 个会话</option></select></label><button class="secondary" :disabled="busy || page <= 1" @click="load(false, page - 1)">上一页</button><button class="secondary" :disabled="busy || page >= pages" @click="load(false, page + 1)">下一页</button></div>
    <AppDialog v-if="detail" :key="detail.id" :title="detail.title" :description="`${detail.owner} · 共 ${detail.total} 轮问答`" :return-focus="opener" :busy="detailBusy" readonly @close="close">
      <p class="trace-conversation-id">会话标识：<span>{{ detail.id }}</span></p>
      <div class="trace-conversation-summary"><span>会话开始 {{ time(detail.first_at) }}</span><span>最近活动 {{ time(detail.last_at) }}</span><span>按时间顺序列出问题，点击展开答复</span></div>
      <p v-if="detailError" class="error" role="alert">{{ detailError }}</p>
      <div ref="rounds" :aria-busy="detailBusy" class="trace-rounds"><TraceMessage v-for="(message,index) in detail.items" :key="message.id" :message="message" :round="(detail.page - 1) * detail.page_size + index + 1" /></div>
      <p v-if="!detail.items.length" class="empty-row">本页暂无问答消息。</p>
      <div v-if="detailPages > 1" class="trace-pagination trace-detail-pagination"><span>第 {{ detail.page }} / {{ detailPages }} 页 · 每页 {{ detail.page_size }} 轮</span><button class="secondary" :disabled="detailBusy || detail.page <= 1" @click="inspect(detail.id, detail.page - 1)">上一页问答</button><button class="secondary" :disabled="detailBusy || detail.page >= detailPages" @click="inspect(detail.id, detail.page + 1)">下一页问答</button></div>
    </AppDialog>
  </section>
</template>

<style scoped>
.trace-conversation-id{margin:0 0 14px;color:#728b95;font-size:12px;overflow-wrap:anywhere}.trace-conversation-id span{user-select:all}
.trace-admin header p{color:var(--muted,#687585)}.trace-admin header{height:auto;padding:0;display:block;border:0;background:transparent}.trace-admin header h2{font-size:28px;margin-bottom:10px}
.trace-filters{display:grid;grid-template-columns:repeat(3,minmax(140px,1fr));gap:14px;align-items:end;margin:22px 0 12px}.trace-filters label{display:grid;gap:6px}.trace-filters input,.trace-filters select{width:100%;min-width:0}.trace-filter-note{font-size:12px;color:#728b95;margin:0 0 20px}
.trace-summary,.trace-pagination{display:flex;gap:18px;align-items:center;flex-wrap:wrap;margin:18px 0}.trace-summary{padding:16px;background:var(--surface,#f4f7fb);border-radius:10px;font-size:13px}.trace-summary strong{color:#315b67}
.trace-table-wrap{overflow-x:auto}.trace-table-wrap table{min-width:850px;width:100%}.trace-table-wrap td{vertical-align:top}.trace-table-wrap td small{display:block;color:#728b95;font-size:11px;margin-top:7px}.trace-topic{max-width:300px;overflow-wrap:anywhere}.trace-topic strong{font-size:14px;color:#315b67}.trace-counts span{display:block;margin-bottom:4px;font-size:12px}.trace-failed{color:#a34336}
.trace-pagination{justify-content:flex-end}.trace-pagination>span{margin-right:auto}.trace-pagination>label{display:flex;flex-direction:row;align-items:center;gap:8px;white-space:nowrap;font-size:12px}.trace-pagination select{width:auto;min-width:110px}.trace-conversation-summary{display:flex;gap:10px 24px;flex-wrap:wrap;color:#728b95;font-size:12px;border-bottom:1px solid #dce7ec;padding-bottom:16px}.trace-detail-pagination{border-top:1px solid #dce7ec;padding-top:18px}.trace-rounds[aria-busy="true"]{opacity:.6}.trace-record{border-top:1px solid #e5edef;margin-top:22px;padding-top:14px;font-size:12px;color:#728b95;overflow-wrap:anywhere}.trace-record>summary{cursor:pointer}.trace-record summary:focus-visible{outline:2px solid #438d99;outline-offset:3px}
@media(max-width:760px){.trace-filters{grid-template-columns:1fr 1fr}.trace-pagination{gap:10px}.trace-pagination>span{flex-basis:100%}}@media(max-width:440px){.trace-filters{grid-template-columns:1fr}}
</style>
