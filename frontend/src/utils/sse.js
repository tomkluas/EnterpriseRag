export class SSEClient {
  constructor(url, body, token, { timeoutMs = 60000 } = {}) {
    this.url = url
    this.body = body
    this.token = token
    this.handlers = {}
    this.controller = new AbortController()
    this.timeoutMs = timeoutMs
  }

  on(event, handler) { this.handlers[event] = handler; return this }
  abort() { this.controller.abort() }

  async start() {
    let timer, reader, terminal = false, timedOut = false
    const resetTimeout = () => {
      clearTimeout(timer)
      timer = setTimeout(() => {
        timedOut = true
        this.controller.abort()
      }, this.timeoutMs)
    }
    const dispatch = frame => {
      const data = frame.split(/\r?\n/).filter(line => line.startsWith('data:'))
        .map(line => line.slice(5).trimStart()).join('\n')
      if (!data) return
      let msg
      try { msg = JSON.parse(data) } catch { throw new Error('服务器返回了无效的流式数据') }
      if (msg.event === 'done' || msg.event === 'error') terminal = true
      this.handlers[msg.event]?.(msg.data)
    }
    resetTimeout()
    try {
      const resp = await fetch(this.url, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${this.token}`,
        },
        body: JSON.stringify(this.body),
        signal: this.controller.signal,
      })
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}))
        const detail = typeof err.detail === 'string' ? err.detail : `请求失败（HTTP ${resp.status}）`
        throw new Error(resp.status === 401 ? '登录已过期，请重新登录' : detail)
      }
      if (!resp.body) throw new Error('服务器未返回响应流')
      reader = resp.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      while (!terminal) {
        const { done, value } = await reader.read()
        if (done) {
          buffer += decoder.decode()
          if (buffer.trim()) dispatch(buffer)
          break
        }
        resetTimeout()
        buffer += decoder.decode(value, { stream: true })
        const frames = buffer.split(/\r?\n\r?\n/)
        buffer = frames.pop()
        for (const frame of frames) {
          dispatch(frame)
          if (terminal) break
        }
      }
      if (!terminal && !this.controller.signal.aborted) throw new Error('连接已中断，回答尚未完成，请重试')
    } catch (err) {
      if (!terminal && (timedOut || !this.controller.signal.aborted)) {
        this.handlers.error?.(timedOut ? '等待响应超时，请检查后端服务或网络后重试' : (err.message || '网络连接失败'))
      }
    } finally {
      clearTimeout(timer)
      if (reader) {
        await reader.cancel().catch(() => {})
        reader.releaseLock()
      }
    }
  }
}
