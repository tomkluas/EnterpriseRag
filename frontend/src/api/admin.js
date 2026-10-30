import http from './http.js'

export const adminApi = {
  // LLM Configs
  getLLMConfigs: () => http.get('/admin/llm-configs'),
  createLLMConfig: (data) => http.post('/admin/llm-configs', data),
  setDefaultLLM: (id) => http.put(`/admin/llm-configs/${id}/set-default`),
  deleteLLMConfig: (id) => http.delete(`/admin/llm-configs/${id}`),
  testLLMConfig: (configId) => http.post('/admin/llm-configs/test', { config_id: configId }),

  // DB Connections
  getDBConnections: () => http.get('/admin/db-connections'),
  createDBConnection: (data) => http.post('/admin/db-connections', data),
  deleteDBConnection: (id) => http.delete(`/admin/db-connections/${id}`),
  testDBConnection: (id) => http.post(`/admin/db-connections/${id}/test`),
  refreshSchema: (id) => http.post(`/admin/db-connections/${id}/refresh-schema`),

  // Users
  getUsers: () => http.get('/admin/users'),
  createUser: (data) => http.post('/admin/users', data),
  toggleUserActive: (id) => http.put(`/admin/users/${id}/toggle-active`),

  // System Info
  getSystemInfo: () => http.get('/admin/system-info'),

  // Embedding / Search 系统设置（设置页二次修改用）
  getEmbeddingConfig: () => http.get('/admin/embedding-config'),
  updateEmbeddingConfig: (data) => http.put('/admin/embedding-config', data),
  getSearchConfig: () => http.get('/admin/search-config'),
  updateSearchConfig: (data) => http.put('/admin/search-config', data),
}
