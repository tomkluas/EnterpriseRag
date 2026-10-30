<template>
  <div class="message" :class="msg.role">
    <div class="bubble">
      <div
        v-if="msg.role === 'assistant'"
        class="md-content"
        v-html="sanitized"
      />
      <div v-else class="plain">{{ msg.content }}</div>
    </div>
    <div v-if="msg.sources && msg.sources.length" class="sources">
      <p class="sources-label">参考来源（{{ msg.sources.length }}）</p>
      <SourceCard v-for="(s, i) in msg.sources" :key="i" :source="s" />
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import DOMPurify from 'dompurify'
import { renderMarkdown } from '@/utils/markdown.js'
import SourceCard from './SourceCard.vue'

const props = defineProps({ msg: Object })

const sanitized = computed(() =>
  DOMPurify.sanitize(renderMarkdown(props.msg.content))
)
</script>

<style scoped>
.message { margin-bottom: 20px; }
.message.user { display: flex; flex-direction: column; align-items: flex-end; }
.message.assistant { display: flex; flex-direction: column; align-items: flex-start; }
.bubble {
  max-width: 75%; padding: 12px 16px; border-radius: 12px; line-height: 1.6;
}
.user .bubble { background: #3b82f6; color: white; border-bottom-right-radius: 4px; }
.assistant .bubble {
  background: white; border: 1px solid #e5e7eb;
  border-bottom-left-radius: 4px; color: #111827;
}
.plain { white-space: pre-wrap; word-break: break-word; }
.md-content :deep(p) { margin-bottom: 0.5em; }
.md-content :deep(pre) {
  background: #1e1e2e; color: #cdd6f4; padding: 12px; border-radius: 8px;
  overflow-x: auto; font-size: 0.85rem; margin: 8px 0;
}
.md-content :deep(code) { font-family: 'Fira Code', monospace; font-size: 0.88em; }
.md-content :deep(ul), .md-content :deep(ol) { padding-left: 1.4em; margin: 6px 0; }
.sources { max-width: 75%; margin-top: 6px; }
.sources-label { font-size: 0.78rem; color: #9ca3af; margin-bottom: 4px; }
</style>
