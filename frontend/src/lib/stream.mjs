// 将字节还原为 SSE 事件；网络分块不等于字符或完整事件边界。
export async function* readEvents(body) {
  if (!body) throw new Error('服务器未返回回答流')
  const reader=body.getReader(),decoder=new TextDecoder()
  let buffer='',terminal=false
  try {
    while (!terminal) {
      const {done,value}=await reader.read()
      // 流式解码保留跨块 UTF-8 字节，未完整事件帧留待下次读取。
      buffer+=done?decoder.decode():decoder.decode(value,{stream:true})
      const frames=buffer.split(/\r?\n\r?\n/);buffer=frames.pop()
      // 最后一个分隔段可能只是半帧；只有底层结束时才把无空行结尾的剩余内容作为末帧处理。
      if(done&&buffer.trim())frames.push(buffer)
      for(const frame of frames){
        // 仅合并 data 行，忽略心跳注释；多行数据按换行拼接。
        const payload=frame.split(/\r?\n/).filter(x=>x.startsWith('data:')).map(x=>x.slice(5).trimStart()).join('\n')
        if(!payload)continue
        let event;try{event=JSON.parse(payload)}catch{throw new Error('服务器返回了无效数据')}
        // done 和 error 都结束协议，终止事件仍交给调用方处理。
        terminal=event.event==='done'||event.event==='error'
        // 先记终止再 yield，消费方拿到事件后退出时，finally 仍能取消读取并回收连接。
        yield event
        if(terminal)break
      }
      if(done)break
    }
    // 网络结束不代表业务完成，缺少终止事件时报告中断。
    if(!terminal)throw new Error('连接中断，回答尚未完成')
  // 完成、消费方退出和解析异常都释放读取器，避免连接占用。
  } finally {await reader.cancel().catch(()=>{});reader.releaseLock()}
}
