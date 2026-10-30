import http from './http'

export async function sendChatMessage(body) {
  return http.post('/v1/chat/stream', body)
}