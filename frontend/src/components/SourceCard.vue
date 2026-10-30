<template>
  <div class="source-card" :class="{ expanded }">
    <div class="source-header" @click="expanded = !expanded">
      <span class="icon">{{ iconMap[source.source_type] || '📄' }}</span>
      <span class="name">{{ source.source_name }}</span>
      <span class="detail">{{ source.source_detail }}</span>
      <span class="chevron">{{ expanded ? '▲' : '▼' }}</span>
    </div>
    <div v-if="expanded" class="source-body">
      <pre>{{ source.text }}</pre>
      <a v-if="source.source_type === 'web'" :href="source.source_detail" target="_blank" rel="noopener">
        🔗 查看原文
      </a>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
defineProps({ source: Object })
const expanded = ref(false)
const iconMap = { vector: '📄', database: '🗄️', web: '🌐' }
</script>

<style scoped>
.source-card {
  border: 1px solid #e5e7eb; border-radius: 8px;
  margin-top: 6px; font-size: 0.82rem; overflow: hidden;
}
.source-header {
  display: flex; align-items: center; gap: 6px; padding: 7px 10px;
  cursor: pointer; background: #f9fafb; user-select: none;
}
.source-header:hover { background: #f3f4f6; }
.icon { font-size: 1rem; }
.name { font-weight: 500; color: #374151; flex: 1; }
.detail { color: #9ca3af; font-size: 0.78rem; }
.chevron { color: #9ca3af; font-size: 0.7rem; margin-left: 4px; }
.source-body { padding: 10px 12px; background: white; border-top: 1px solid #e5e7eb; }
pre {
  white-space: pre-wrap; word-break: break-word; font-family: inherit;
  color: #374151; font-size: 0.82rem; line-height: 1.5; margin-bottom: 6px;
}
a { color: #3b82f6; text-decoration: none; font-size: 0.8rem; }
a:hover { text-decoration: underline; }
</style>
