export async function* readEvents(body) {
  if (!body) throw new Error('服务器未返回回答流')
  const reader=body.getReader(),decoder=new TextDecoder()
  let buffer='',terminal=false
  try {
    while (!terminal) {
      const {done,value}=await reader.read()
      buffer+=done?decoder.decode():decoder.decode(value,{stream:true})
      const frames=buffer.split(/\r?\n\r?\n/);buffer=frames.pop()
      if(done&&buffer.trim())frames.push(buffer)
      for(const frame of frames){
        const payload=frame.split(/\r?\n/).filter(x=>x.startsWith('data:')).map(x=>x.slice(5).trimStart()).join('\n')
        if(!payload)continue
        let event;try{event=JSON.parse(payload)}catch{throw new Error('服务器返回了无效数据')}
        terminal=event.event==='done'||event.event==='error'
        yield event
        if(terminal)break
      }
      if(done)break
    }
    if(!terminal)throw new Error('连接中断，回答尚未完成')
  } finally {await reader.cancel().catch(()=>{});reader.releaseLock()}
}
