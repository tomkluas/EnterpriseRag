import http from './http'

export async function getLLMConfigs() {
  return http.get('/v1/admin/llm-configs')
}

export async function createLLMConfig(data) {
  return http.post('/v1/admin/llm-configs', data)
}

export async function setDefaultLLMConfig(configId) {
  return http.put(`/v1/admin/llm-configs/${configId}/set-default`)
}

export async function deleteLLMConfig(configId) {
  return http.delete(`/v1/admin/llm-configs/${configId}`)
}

export async function testLLMConfig(configId) {
  return http.post('/v1/admin/llm-configs/test', null, { params: { config_id: configId } })
}

export async function getDBConnections() {
  return http.get('/v1/admin/db-connections')
}

export async function createDBConnection(data) {
  return http.post('/v1/admin/db-connections', data)
}

export async function deleteDBConnection(connId) {
  return http.delete(`/v1/admin/db-connections/${connId}`)
}

export async function testDBConnection(connId) {
  return http.post(`/v1/admin/db-connections/${connId}/test`)
}

export async function refreshSchema(connId) {
  return http.post(`/v1/admin/db-connections/${connId}/refresh-schema`)
}

export async function getUsers() {
  return http.get('/v1/admin/users')
}

export async function createUser(username, password, role) {
  return http.post('/v1/admin/users', { username, password, role })
}

export async function toggleUserActive(userId) {
  return http.put(`/v1/admin/users/${userId}/toggle-active`)
}
