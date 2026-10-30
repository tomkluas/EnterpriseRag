<template>
  <div class="source-card" @click="expanded = !expanded">
    <div class="source-header">
      <span class="source-icon">{{ icon }}</span>
      <span class="source-name">{{ source.source_name }}</span>
      <span class="source-detail">{{ source.source_detail }}</span>
      <span class="expand-icon">{{ expanded ? '▲' : '▼' }}</span>
    </div>
    <div v-if="expanded" class="source-body">
      <pre class="source-text">{{ source.text }}</pre>
      <a v-if="source.source_type === 'web'" :href="source.source_detail" target="_blank" class="source-link">
        🔗 查看原文
      </a>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'

const props = defineProps({
  source: {
    type: Object,
    required: true,
  },
})

const expanded = ref(false)

const icon = computed(() => {
  const icons = { vector: '📄', database: '🗄️', web: '🌐' }
  return icons[props.source.source_type] || '📄'
})
</script>

<style scoped>
.source-card {
  background: #f8f9fa;
  border: 1px solid #e9ecef;
  border-radius: 4px;
  cursor: pointer;
  font-size: 13px;
}

.source-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
}

.source-icon {
  font-size: 16px;
}

.source-name {
  font-weight: 600;
  color: #333;
}

.source-detail {
  color: #666;
  flex: 1;
}

.expand-icon {
  color: #999;
  font-size: 10px;
}

.source-body {
  padding: 12px;
  border-top: 1px solid #e9ecef;
}

.source-text {
  white-space: pre-wrap;
  word-break: break-word;
  font-family: inherit;
  font-size: 12px;
  color: #555;
  margin: 0;
}

.source-link {
  display: inline-block;
  margin-top: 8px;
  color: #3498db;
  text-decoration: none;
}
</style>