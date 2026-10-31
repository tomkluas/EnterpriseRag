import test from 'node:test'
import assert from 'node:assert/strict'
import { readEvents } from '../src/lib/stream.mjs'

test('UTF-8 fragments and heartbeats preserve response text', async () => {
  const bytes=new TextEncoder().encode(': heartbeat\n\ndata: {"event":"token","data":"资料"}\n\ndata: {"event":"done","data":{}}\n\n')
  const stream=new ReadableStream({ start(c){for(let i=0;i<bytes.length;i+=3)c.enqueue(bytes.slice(i,i+3));c.close()} })
  const events=[]; for await(const event of readEvents(stream))events.push(event)
  assert.equal(events[0].data,'资料');assert.equal(events[1].event,'done')
})

test('EOF without completion fails instead of displaying success',async()=>{
  const stream=new ReadableStream({start(c){c.enqueue(new TextEncoder().encode('data: {"event":"token","data":"partial"}\n\n'));c.close()}})
  await assert.rejects(async()=>{for await(const _ of readEvents(stream)){}},/尚未完成/)
})
