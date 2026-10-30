<template>
  <div class="model-selector">
    <label>模型：</label>
    <select :value="config.selectedConfigId" @change="config.selectConfig($event.target.value || null)">
      <option value="">默认模型</option>
      <option v-for="c in config.llmConfigs" :key="c.id" :value="c.id">
        {{ c.name }}
      </option>
    </select>
  </div>
</template>

<script setup>
import { onMounted } from 'vue'
import { useConfigStore } from '@/stores/config.js'
const config = useConfigStore()
onMounted(() => config.fetchConfigs())
</script>

<style scoped>
.model-selector { display: flex; align-items: center; gap: 8px; }
label { font-size: 0.82rem; color: #6b7280; white-space: nowrap; }
select {
  font-size: 0.82rem; padding: 4px 8px; border: 1px solid #d1d5db;
  border-radius: 6px; background: white; color: #374151; cursor: pointer;
}
</style>
