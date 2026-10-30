import { defineStore } from 'pinia'
import { ref } from 'vue'

export const useConfigStore = defineStore('config', () => {
  const llmConfigs = ref([])
  const currentConfigId = ref(null)

  function setConfigs(configs) {
    llmConfigs.value = configs
    const defaultConfig = configs.find(c => c.is_default)
    if (defaultConfig) {
      currentConfigId.value = defaultConfig.id
    }
  }

  function selectConfig(configId) {
    currentConfigId.value = configId
  }

  return {
    llmConfigs,
    currentConfigId,
    setConfigs,
    selectConfig,
  }
})