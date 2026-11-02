<script setup>
import MarkdownIt from 'markdown-it'
import DOMPurify from 'dompurify'

defineProps({ message: { type: Object, required: true }, round: { type: Number, required: true } })
const labels = { completed: '已完成', failed: '失败', processing: '处理中' }
const stages = { request: '整体处理', rewrite: '问题改写', retrieve: '检索资料', rerank: '证据精排', generate: '生成回答' }
const outcomes = { ok: '完成', error: '失败', cancelled: '已中止' }
const time = value => new Date(value * 1000).toLocaleString('zh-CN')
const duration = value => value == null ? '暂无记录' : `${value} ms`
const md = new MarkdownIt({ html: false, linkify: true, breaks: true })
// 审计记录也属于不可信内容，Markdown 禁止原生 HTML，渲染后再清理生成标记。
const render = value => DOMPurify.sanitize(md.render(value || ''))
</script>

<template>
  <article class="trace-round" :aria-label="`第 ${round} 轮问答`">
    <div class="trace-question-line"><strong>第 {{ round }} 轮</strong><p class="trace-plain">{{ message.query }}</p></div>
    <div class="trace-round-meta"><time>{{ time(message.created_at) }}</time><span class="trace-message-id">消息标识：{{ message.id }}</span><span class="badge" :class="message.state">{{ labels[message.state] || message.state }}</span></div>
    <details class="trace-fold trace-answer"><summary><span class="answer-closed">展开答复</span><span class="answer-open">收起答复</span></summary><div class="trace-fold-body"><div v-if="message.answer" class="markdown" v-html="render(message.answer)"></div><p v-else class="muted">{{ message.state === 'processing' ? '本轮问答仍在处理中。' : '本轮没有生成回答。' }}</p><p v-if="message.trace?.failure" class="error" role="alert">{{ message.trace.failure.detail }}（{{ message.trace.failure.code }}）</p><section v-if="message.feedback" class="trace-feedback"><h3>成员反馈</h3><p class="trace-plain">{{ message.feedback }}</p></section></div></details>
    <details class="trace-fold"><summary>来源证据 <span class="badge">{{ message.evidence.length }} 条</span></summary><div class="trace-fold-body">
      <details v-for="(source,index) in message.evidence" :key="index" class="trace-evidence"><summary>来源 {{ index + 1 }}<span v-if="source.locator?.page"> · 第 {{ source.locator.page }} 页</span></summary><div class="trace-evidence-body"><p class="trace-identifiers">资料：{{ source.document_id }}<br>版本：{{ source.version_id }}</p><p v-if="Object.keys(source.locator || {}).length" class="trace-locator">位置：{{ Object.entries(source.locator).map(([key,value]) => `${key}: ${value}`).join('，') }}</p><blockquote class="trace-plain">{{ source.content }}</blockquote></div></details>
      <p v-if="!message.evidence.length" class="muted">本轮问答没有来源证据。</p>
    </div></details>
    <details class="trace-fold"><summary>处理明细 <span>整体耗时 {{ duration(message.total_milliseconds) }}</span></summary><div class="trace-fold-body"><table v-if="message.trace?.stages?.length"><thead><tr><th>阶段</th><th>耗时</th><th>结果</th></tr></thead><tbody><tr v-for="(step,index) in message.trace.stages" :key="index"><td>{{ stages[step.stage] || step.stage }}</td><td>{{ duration(step.milliseconds) }}</td><td>{{ outcomes[step.outcome] || step.outcome }}</td></tr></tbody></table><p v-else class="muted">本轮暂无处理明细，历史回答不会补生成记录。</p></div></details>
  </article>
</template>

<style scoped>
.trace-round{padding:24px 0;border-bottom:1px solid #dce7ec;overflow-wrap:anywhere}.trace-round:last-child{border-bottom:0}
.trace-question-line{display:flex;align-items:baseline;gap:12px}.trace-question-line>strong{flex-shrink:0;color:#315b67;font-size:14px}.trace-question-line>p{margin:0;min-width:0;color:#263a40;font-size:14px;line-height:1.8}.trace-round-meta{display:flex;align-items:center;flex-wrap:wrap;gap:6px 16px;margin:9px 0 16px;color:#728b95;font-size:11px}.trace-message-id{overflow-wrap:anywhere;min-width:0;user-select:all}.trace-round-meta .badge{margin-left:auto}.trace-round h3{font-size:12px;color:#617b85;margin:0 0 8px}.trace-answer .markdown{line-height:1.9}
.trace-plain{white-space:pre-wrap;overflow-wrap:anywhere}.trace-fold{border:1px solid #dce7ec;border-radius:8px;margin:12px 0;background:#fff}.trace-fold>summary{display:flex;align-items:center;gap:12px;cursor:pointer;padding:14px;color:#315b67;font-size:13px}.trace-fold>summary>span:last-child{margin-left:auto;font-size:12px;color:#728b95}.trace-fold[open]>summary{border-bottom:1px solid #e5edef;background:#f4f8fa}.trace-fold-body{padding:16px}.trace-fold-body>p:last-child{margin-bottom:0}.trace-fold table{width:100%}
.trace-evidence{border:1px solid #dce7ec;border-radius:5px;margin-bottom:10px}.trace-evidence:last-child{margin-bottom:0}.trace-evidence>summary{padding:11px 13px;cursor:pointer;background:#f5f8fa;font-size:12px;color:#526d78}.trace-evidence-body{padding:14px}.trace-evidence blockquote{margin:14px 0 0;border-left:3px solid #bdd3db;padding-left:12px;line-height:1.9;color:#405862}.trace-identifiers,.trace-locator{font-size:11px;color:#728b95;overflow-wrap:anywhere}.trace-feedback{margin-top:20px;padding:14px;background:#f5f8fa;border-radius:6px}.trace-feedback p{margin:0}.trace-record{margin-top:18px;font-size:12px;color:#728b95}.trace-record summary{cursor:pointer}.trace-record p{margin-top:10px}
summary:focus-visible{outline:2px solid #438d99;outline-offset:3px;border-radius:4px}
.trace-answer{padding:0}.trace-answer>summary::before{content:'▸';font-size:12px}.trace-answer[open]>summary::before{content:'▾'}.trace-answer .answer-open{display:none}.trace-answer[open] .answer-open{display:inline}.trace-answer[open] .answer-closed{display:none}.trace-answer>summary>span:last-child{margin-left:0;font-size:13px;color:inherit}
</style>
