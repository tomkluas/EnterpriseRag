<template>
  <div class="model-selector">
    <select v-model="selected" @change="emit('change', selected)">
      <option v-for="cfg in configs" :key="cfg.id" :value="cfg.id">
        {{ cfg.name }}
      </option>
    </select>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useConfigStore } from '@/stores/config'
import { getLLMConfigs } from '@/api/admin'

const configStore = useConfigStore()
const configs = ref([])
const selected = ref(null)

const emit = defineEmits(['change'])

async function load() {
  try {
    configs.value = await getLLMConfigs()
    configStore.setConfigs(configs.value)
    if (configs.value.length) {
      selected.value = configs.value.find(c => c.is_default)?.id || configs.value[0].id
    }
  } catch (e) {
    console.error('Failed to load LLM configs', e)
  }
}

onMounted(load)
</script>

<style scoped>
.model-selector select {
  padding: 8px 12px;
  border: 1px solid #ddd;
  border-radius: 4px;
  background: white;
  cursor: pointer;
}
</style>