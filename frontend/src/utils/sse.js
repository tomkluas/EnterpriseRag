import { errorMessage } from './errors.js'

export class SSEClient {
  constructor(url, body, token) {
    this.url = url
    this.body = body
    this.token = token
    this.handlers = {}
    this.controller = new AbortController()
  }

  on(event, handler) {
    this.handlers[event] = handler
    return this
  }

  abort() { this.controller.abort() }

  async start() {
    let reader
    let terminal = false
    const dispatch = frame => {
      const data = frame.split(/\r?\n/).filter(line => line.startsWith('data:'))
        .map(line => line.slice(5).trimStart()).join('\n')
      if (!data) return
      const msg = JSON.parse(data)
      if (msg.event === 'done' || msg.event === 'error') terminal = true
      this.handlers[msg.event]?.(msg.event === 'error' ? errorMessage(msg.data) : msg.data)
    }
    try {
      const resp = await fetch(this.url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${this.token}` },
        body: JSON.stringify(this.body),
        signal: this.controller.signal,
      })
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}))
        if (resp.status === 401) this.handlers.unauthorized?.()
        this.handlers.error?.(errorMessage(err, `请求失败 (${resp.status})`))
        return
      }
      if (!resp.body) throw new Error('服务器未返回数据流')
      reader = resp.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      while (!terminal) {
        const { done, value } = await reader.read()
        buffer += done ? decoder.decode() : decoder.decode(value, { stream: true })
        let separator
        while (!terminal && (separator = /\r?\n\r?\n/.exec(buffer))) {
          const frame = buffer.slice(0, separator.index)
          buffer = buffer.slice(separator.index + separator[0].length)
          dispatch(frame)
        }
        if (done) {
          if (!terminal && buffer.trim()) dispatch(buffer)
          if (!terminal) throw new Error('连接已断开，回答未完成，请重试')
          break
        }
      }
    } catch (error) {
      if (error.name !== 'AbortError' && !this.controller.signal.aborted) this.handlers.error?.(errorMessage(error))
    } finally {
      try { await reader?.cancel() } catch {}
      reader?.releaseLock()
      this.handlers.close?.()
    }
  }
}
