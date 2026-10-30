import http from './http.js'

export const setupApi = {
  getStatus: () => http.get('/setup/status'),
  setAdmin: (password) => http.post('/setup/admin', { password }),
  setLLM: (provider, api_key, model_string) =>
    http.post('/setup/llm', { provider, api_key, model_string }),
  testLLM: (provider, api_key, model_string) =>
    http.post('/setup/llm/test', { provider, api_key, model_string }),
  setEmbedding: (provider, api_key, model) =>
    http.post('/setup/embedding', { provider, api_key, model }),
  setSearch: (provider, api_key) =>
    http.post('/setup/search', { provider, api_key }),
  complete: () => http.post('/setup/complete'),
}
