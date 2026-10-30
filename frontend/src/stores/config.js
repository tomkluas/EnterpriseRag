import { defineStore } from 'pinia'
import { ref } from 'vue'
import { adminApi } from '@/api/admin.js'

export const useConfigStore = defineStore('config', () => {
  const llmConfigs = ref([])
  const selectedConfigId = ref(null)

  async function fetchConfigs() {
    try {
      const data = await adminApi.getLLMConfigs()
      llmConfigs.value = data
      const def = data.find(c => c.is_default)
      if (def && !selectedConfigId.value) {
        selectedConfigId.value = def.id
      }
    } catch (e) {
      // ignore if not admin
    }
  }

  function selectConfig(id) {
    selectedConfigId.value = id
  }

  return { llmConfigs, selectedConfigId, fetchConfigs, selectConfig }
})
