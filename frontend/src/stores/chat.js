import { defineStore } from 'pinia'
import { ref } from 'vue'
import { v4 as uuidv4 } from 'uuid'

export const useChatStore = defineStore('chat', () => {
  const sessions = ref([])
  const currentSessionId = ref(null)

  function newSession() {
    const session = {
      id: uuidv4(),
      messages: [],
      createdAt: new Date().toISOString(),
    }
    sessions.value.unshift(session)
    currentSessionId.value = session.id
    return session.id
  }

  function currentSession() {
    return sessions.value.find(s => s.id === currentSessionId.value)
  }

  function addMessage(msg) {
    const session = currentSession()
    if (!session) return null

    const message = {
      id: uuidv4(),
      role: msg.role,
      content: msg.content || '',
      sources: msg.sources || [],
      createdAt: new Date().toISOString(),
    }
    session.messages.push(message)
    return message.id
  }

  function appendToken(msgId, token) {
    const msg = sessions.value.flatMap(s => s.messages).find(m => m.id === msgId)
    if (msg) {
      msg.content += token
    }
  }

  function addSource(msgId, source) {
    const msg = sessions.value.flatMap(s => s.messages).find(m => m.id === msgId)
    if (!msg) return

    const key = `${source.source_name}::${source.source_detail}`
    if (!msg.sources.some(s => `${s.source_name}::${s.source_detail}` === key)) {
      msg.sources.push(source)
    }
  }

  function selectSession(sessionId) {
    currentSessionId.value = sessionId
  }

  function clearSessions() {
    sessions.value = []
    currentSessionId.value = null
  }

  return {
    sessions,
    currentSessionId,
    newSession,
    currentSession,
    addMessage,
    appendToken,
    addSource,
    selectSession,
    clearSessions,
  }
})
