<template>
  <div :class="['message-bubble', message.role]">
    <div class="message-content" v-html="renderedContent"></div>
    <div v-if="message.sources?.length" class="sources">
      <SourceCard
        v-for="(source, idx) in message.sources"
        :key="idx"
        :source="source"
      />
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import DOMPurify from 'dompurify'
import { renderMarkdown } from '@/utils/markdown'
import SourceCard from './SourceCard.vue'

const props = defineProps({
  message: {
    type: Object,
    required: true,
  },
})

const renderedContent = computed(() => {
  const html = renderMarkdown(props.message.content || '')
  return DOMPurify.sanitize(html)
})
</script>

<style scoped>
.message-bubble {
  margin-bottom: 20px;
  padding: 16px;
  border-radius: 8px;
  max-width: 800px;
}

.message-bubble.user {
  background: #3498db;
  color: white;
  margin-left: auto;
}

.message-bubble.assistant {
  background: white;
  box-shadow: 0 2px 8px rgba(0,0,0,0.1);
}

.message-content {
  line-height: 1.6;
  word-break: break-word;
}

.sources {
  margin-top: 16px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
</style>
