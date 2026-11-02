import test from 'node:test'
import assert from 'node:assert/strict'
import { navigationFor } from '../src/lib/navigation.mjs'

const ids=permissions=>navigationFor(permissions).map(item=>item.id)

test('ordinary readers can only enter knowledge chat',()=>{
  assert.deepEqual(ids(['knowledge.read']),['chat'])
  assert.deepEqual(ids(),['chat'])
})

test('workspace navigation follows capabilities rather than fixed role names',()=>{
  for(const permission of ['knowledge.create','knowledge.manage','system.manage']) {
    const pages=ids(['knowledge.read',permission])
    assert.ok(pages.includes('documents'))
    assert.ok(pages.includes('jobs'))
  }
  assert.deepEqual(ids(['knowledge.read','traces.view']),['chat','traces'])
  assert.deepEqual(ids(['members.manage']),['chat','directory'])
  assert.deepEqual(ids(['knowledge.read','system.manage']),['chat','documents','backends','jobs','directory','audit'])
})
