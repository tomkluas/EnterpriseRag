import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { parse, compileScript } from '@vue/compiler-sfc'
import { renderToString } from '@vue/server-renderer'
import * as Vue from 'vue'

async function compileComponent(filename, dependencies = {}) {
  const source = await readFile(new URL(`../src/components/${filename}`, import.meta.url), 'utf8')
  const { descriptor } = parse(source)
  let code = compileScript(descriptor, { id: filename, inlineTemplate: true }).content
  code = code.replace(/import \{([^}]+)\} from ['"]vue['"]/g, (_, names) =>
    `const {${names.replace(/\s+as\s+/g, ':')}} = Vue`)
  code = code.replace(/^import .* from ['"](?:dompurify|@\/utils\/markdown|.*SourceCard.vue)['"]\r?$/gm, '')
  code = code.replace('export default', 'return')
  return new Function('Vue', ...Object.keys(dependencies), code)(Vue, ...Object.values(dependencies))
}

test('assistant bubble renders actual source cards with document name and page', async () => {
  const SourceCard = await compileComponent('SourceCard.vue')
  const MessageBubble = await compileComponent('MessageBubble.vue', {
    SourceCard,
    DOMPurify: { sanitize: html => html },
    renderMarkdown: text => `<p>${text}</p>`,
  })
  const app = Vue.createSSRApp(MessageBubble, {
    message: {role: 'assistant', content: '已找到相关岗位', sources: [
      {source_type: 'vector', source_name: '岗位职责说明.docx', source_detail: '第3页', text: '原始资料'},
      {source_type: 'database', source_name: '招聘数据库', source_detail: 'jobs', text: '查询结果'},
    ]},
  })
  const warnings = []
  app.config.warnHandler = message => warnings.push(message)
  const html = await renderToString(app)
  assert.match(html, /岗位职责说明\.docx/)
  assert.match(html, /第3页/)
  assert.match(html, /招聘数据库/)
  assert.match(html, /jobs/)
  assert.equal((html.match(/class="source-card"/g) || []).length, 2)
  assert.deepEqual(warnings, [])
})
