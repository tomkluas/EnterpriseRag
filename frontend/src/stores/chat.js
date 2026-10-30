import { defineStore } from 'pinia'
import { ref } from 'vue'
import { v4 as uuidv4 } from 'uuid'

export const useChatStore = defineStore('chat', () => {
  const sessions = ref([])
  const currentSessionId = ref(null)

  function newSession() {
    const id = uuidv4()
    sessions.value.unshift({ id, messages: [], createdAt: new Date().toISOString() })
    currentSessionId.value = id
    return id
  }

  function currentSession() {
    return sessions.value.find(s => s.id === currentSessionId.value)
  }

  function switchSession(id) {
    currentSessionId.value = id
  }

  function addMessage(msg) {
    const session = currentSession()
    if (!session) return ''
    const msgId = uuidv4()
    session.messages.push({
      id: msgId,
      role: msg.role,
      content: msg.content || '',
      sources: msg.sources || [],
    })
    return msgId
  }

  function appendToken(msgId, token) {
    const msg = sessions.value.flatMap(s => s.messages).find(m => m.id === msgId)
    if (msg) msg.content += token
  }

  function addSource(msgId, source) {
    const msg = sessions.value.flatMap(s => s.messages).find(m => m.id === msgId)
    if (!msg) return
    const key = `${source.source_name}__${source.source_detail}`
    if (!msg.sources.some(s => `${s.source_name}__${s.source_detail}` === key)) {
      msg.sources.push(source)
    }
  }

  function deleteSession(id) {
    sessions.value = sessions.value.filter(s => s.id !== id)
    if (currentSessionId.value === id) {
      currentSessionId.value = sessions.value[0]?.id || null
    }
  }

  return {
    sessions, currentSessionId,
    newSession, currentSession, switchSession,
    addMessage, appendToken, addSource, deleteSession,
  }
})
