import http from './http.js'

export const knowledgeApi = {
  list: () => http.get('/knowledge/list'),
  create: (name, description) => http.post('/knowledge/create', { name, description }),
  listDocs: (kbId) => http.get(`/knowledge/${kbId}/documents`),
  upload: (kbId, file) => {
    const form = new FormData()
    form.append('file', file)
    return http.post(`/knowledge/${kbId}/upload`, form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
  },
  deleteDoc: (kbId, docId) => http.delete(`/knowledge/${kbId}/documents/${docId}`),
  deleteKb: (kbId) => http.delete(`/knowledge/${kbId}`),
}
