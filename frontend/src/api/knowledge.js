import http from './http'

export async function listKnowledgeBases() {
  return http.get('/v1/knowledge/list')
}

export async function createKnowledgeBase(name, description) {
  return http.post('/v1/knowledge/create', null, { params: { name, description } })
}

export async function uploadDocument(kbId, file) {
  const formData = new FormData()
  formData.append('file', file)
  return http.post(`/v1/knowledge/${kbId}/upload`, formData, { timeout: 120000 })
}

export async function listDocuments(kbId) {
  return http.get(`/v1/knowledge/${kbId}/documents`)
}

export async function deleteDocument(kbId, docId) {
  return http.delete(`/v1/knowledge/${kbId}/documents/${docId}`)
}

export async function deleteKnowledgeBase(kbId) {
  return http.delete(`/v1/knowledge/${kbId}`)
}
