<script setup>
import { computed,nextTick,onUnmounted,ref,watch } from 'vue'
import AppDialog from './AppDialog.vue'
import { api } from '../lib/api'
const props=defineProps({jobs:{type:Array,default:()=>[]},busy:Boolean})
const emit=defineEmits(['refresh','retry'])
const search=ref(''),state=ref(''),kind=ref(''),detailId=ref(''),opener=ref(null)
const preview=ref(null),previewReplica=ref(''),previewBusy=ref(false),previewError=ref(''),previewTarget=ref(1)
const previewPanel=ref(null)
let previewController=null,previewGeneration=0,disposed=false
const kinds={ingest:'资料入库',replicate:'底座同步',delete:'副本清理',orphan_gc:'孤立原件回收'}
const states={queued:'等待处理',running:'正在处理',completed:'已完成',failed:'失败',needs_reconciliation:'待核对上传结果',maintenance:'维护处理中',cancelled:'已取消'}
const indexStates={received:'准备上传',submitting:'已开始提交，等待回执',backend_processing:'底座正在建立索引',ready:'索引已就绪',delete_pending:'待清理',delete_requested:'等待删除确认',deleted:'已清理',failed:'失败'}
const backendStates={completed:'已完成',ready:'已就绪',processing:'处理中',pending:'等待解析',parsing:'正在解析',pre_processing:'正在预处理',cleaning:'正在清理文本',splitting:'正在分块',embedding:'正在生成索引向量',indexing:'正在索引',waiting:'等待处理',error:'处理错误',failed:'失败',paused:'已暂停',cancelled:'已取消'}
const chunkTypes={text:'正文',parent_text:'父级正文',image_ocr:'图片识别文字',image_caption:'图片描述',summary:'摘要',faq:'问答片段'}
const versionStates={received:'已接收',ready:'可发布',published:'已发布',failed:'失败',cancelled:'已取消'}
const providers={weknora:'WeKnora',dify:'Dify',unknown:'未知底座'}
const errors={invalid_configuration:'底座配置不完整或不可用',parse_failed:'解析失败或没有有效分块',backend_unavailable:'知识服务暂不可用',permission_denied:'知识服务凭据权限不足',timeout_unknown:'上传结果不确定，需要核对',needs_reconciliation:'需核对远端上传结果',index_check_retryable:'索引状态查询失败，等待重查',metadata_check_retryable:'授权标识校验失败，等待重查'}
const detail=computed(()=>props.jobs.find(j=>j.id===detailId.value))
const visible=computed(()=>props.jobs.filter(j=>(!state.value||j.state===state.value)&&(!kind.value||j.kind===kind.value)&&(!search.value.trim()||
  [j.file?.filename,j.file?.space_name,j.id,j.owner].some(v=>v?.toLowerCase().includes(search.value.trim().toLowerCase())))))
const failed=computed(()=>props.jobs.filter(j=>['failed','needs_reconciliation'].includes(j.state)).length)
function time(value){return value?new Date(value*1000).toLocaleString():'未记录'}
function stage(job){
  if(job.state==='completed')return job.kind==='delete'?'副本清理完成':'任务处理完成'
  if(job.state==='needs_reconciliation')return '先核对远端结果，避免重复上传'
  if(job.state==='failed')return '处理失败，请查看原因'
  if(job.state==='cancelled')return '已取消，不再继续处理'
  if(job.index_results?.some(r=>r.state==='delete_requested'))return '等待知识服务确认副本已删除'
  if(job.index_results?.some(r=>r.state==='ready'))return '索引已就绪，等待任务收尾'
  const processing=job.index_results?.find(r=>r.state==='backend_processing')
  if(processing)return processing.raw_state==='completed'?'底座处理完成，正在核对索引绑定':backendStates[processing.raw_state]||'等待底座建立索引'
  if(job.index_results?.some(r=>r.state==='submitting'))return '等待上传结果确认'
  return states[job.state]||job.state
}
function chunks(job){
  const results=job.index_results||[]
  if(!results.length)return '暂无索引记录'
  return results.map(r=>`${providers[r.provider]||r.provider}：${r.chunks===null?'待确认':r.chunks+' 块'}`).join('；')
}
function show(job,event){detailId.value=job.id;opener.value=event.currentTarget}

function resetPreview(){
  // 关闭、切换任务或轮询发现撤权时立即清掉正文，旧请求即使返回也不能重新写入。
  previewGeneration++;previewController?.abort();previewController=null
  preview.value=null;previewReplica.value='';previewError.value='';previewBusy.value=false;previewTarget.value=1
}
async function loadChunks(result,targetPage=1){
  if(!detail.value||!result?.can_preview)return
  previewController?.abort();const generation=++previewGeneration,jobId=detail.value.id
  previewController=new AbortController();previewReplica.value=result.id;previewTarget.value=targetPage
  preview.value=null;previewError.value='';previewBusy.value=true
  try{
    const data=await api(`/jobs/${encodeURIComponent(jobId)}/replicas/${encodeURIComponent(result.id)}/chunks?page=${targetPage}&page_size=10`,{signal:previewController.signal})
    // 除取消网络请求外，还核对请求代次及当前任务，防止迟到结果串到另一个任务详情。
    if(!disposed&&generation===previewGeneration&&detail.value?.id===jobId){
      preview.value=data;previewBusy.value=false
      // 切换分块页后回到本页开头，避免保留上一页滚动位置而漏看新分块。
      // 必须先结束加载并渲染正文，否则只有短加载提示时，浏览器无法滚到预览区顶部。
      await nextTick()
      if(!disposed&&generation===previewGeneration)previewPanel.value?.scrollIntoView({block:'start'})
    }
  }catch(error){if(!disposed&&generation===previewGeneration&&error.name!=='AbortError')previewError.value=error.message}
  finally{if(!disposed&&generation===previewGeneration)previewBusy.value=false}
}
function location(chunk){
  const value=chunk.locator||{}
  if(value.partial)return '仅有局部定位，请结合原文核对'
  return [Number.isInteger(value.page)&&value.page>0?`第 ${value.page} 页`:'',value.sheet?`工作表：${value.sheet}`:'',
    value.block?`段落：${value.block}`:'',value.row_start!=null?`行 ${value.row_start}${value.row_end!=null?'—'+value.row_end:''}`:''].filter(Boolean).join(' · ')||'未提供可确认的原文位置'
}
watch(()=>detail.value?.id,resetPreview)
watch(()=>detail.value?.index_results?.find(r=>r.id===previewReplica.value)?.can_preview,allowed=>{if(allowed===false)resetPreview()})
onUnmounted(()=>{disposed=true;resetPreview()})
</script>

<template>
  <section class="content-page jobs-page">
    <div class="page-heading"><div><h1>处理任务</h1><p>跟踪每份文件的入库、同步和清理结果。索引完成后，资料仍需发布才能用于问答。</p></div><button class="secondary" :disabled="busy" @click="emit('refresh')">刷新</button></div>
    <div class="jobs-toolbar"><label>查找任务<input v-model="search" type="search" placeholder="文件名、知识库、提交人或任务编号"></label><label>任务类型<select v-model="kind"><option value="">全部类型</option><option v-for="(name,key) in kinds" :key="key" :value="key">{{ name }}</option></select></label><label>处理状态<select v-model="state"><option value="">全部状态</option><option v-for="(name,key) in states" :key="key" :value="key">{{ name }}</option></select></label><span class="muted">共 {{ jobs.length }} 项 · {{ failed }} 项需关注 · 每 5 秒刷新</span></div>
    <div class="jobs-table"><table><thead><tr><th>文件 / 知识库</th><th>任务与处理阶段</th><th>分块结果</th><th>创建时间</th><th>操作</th></tr></thead><tbody>
      <tr v-for="job in visible" :key="job.id">
        <td><strong>{{ job.file?.filename||'系统维护任务' }}</strong><small>{{ job.file?.space_name||'不关联单个文件' }}</small><small>提交人：{{ job.owner }}</small></td>
        <td><span>{{ kinds[job.kind]||job.kind }}</span> <span class="badge" :class="job.state">{{ states[job.state]||job.state }}</span><small>{{ stage(job) }}</small><small v-if="job.error" class="job-error">{{ errors[job.error]||job.error }}</small></td>
        <td>{{ chunks(job) }}</td><td>{{ time(job.created_at) }}<small>执行 {{ job.attempts }} 次</small></td>
        <td><button class="text-button" @click="show(job,$event)">查看详情</button><button v-if="job.can_retry" class="text-button" :disabled="busy" @click="emit('retry',job)">重试</button></td>
      </tr>
    </tbody></table></div>
    <div v-if="!visible.length" class="empty-row">{{ jobs.length?'没有符合筛选条件的任务。':'暂无处理任务。上传资料后，可在这里跟踪进度。' }}</div>
    <AppDialog v-if="detail" title="任务处理详情" :description="detail.file?.filename||kinds[detail.kind]||'系统维护任务'" readonly :return-focus="opener" @close="detailId=''">
      <dl class="job-facts"><div><dt>任务类型</dt><dd>{{ kinds[detail.kind]||detail.kind }}</dd></div><div><dt>任务状态</dt><dd>{{ states[detail.state]||detail.state }}</dd></div><div><dt>知识库</dt><dd>{{ detail.file?.space_name||'—' }}</dd></div><div><dt>版本状态</dt><dd>{{ versionStates[detail.file?.version_state]||detail.file?.version_state||'—' }}{{ detail.file?.withdrawn?'（资料已撤回）':'' }}</dd></div><div><dt>创建时间</dt><dd>{{ time(detail.created_at) }}</dd></div><div><dt>执行 / 连续失败</dt><dd>{{ detail.attempts }} / {{ detail.failures }} 次</dd></div><div><dt>任务编号</dt><dd>{{ detail.id }}</dd></div><div v-if="detail.file?.version_id"><dt>文件版本编号</dt><dd>{{ detail.file.version_id }}</dd></div><div v-if="detail.state==='queued'&&detail.next_run"><dt>下次检查时间</dt><dd>{{ time(detail.next_run) }}</dd></div></dl>
      <p v-if="detail.error" class="error">{{ errors[detail.error]||detail.error }}<small>错误记录：{{ detail.error }}</small></p>
      <h3>索引与分块结果</h3>
      <div v-for="result in detail.index_results" :key="result.id" class="job-index-result"><div class="job-index-heading"><strong>{{ providers[result.provider]||result.provider }}</strong><span class="badge" :class="result.state">{{ indexStates[result.state]||result.state }}</span><strong>{{ result.chunks===null?'分块数量待确认':result.chunks+' 个分块' }}</strong></div><p v-if="result.error" class="job-error">{{ errors[result.error]||result.error }}</p><dl class="job-facts"><div><dt>底座处理状态</dt><dd>{{ backendStates[result.raw_state]||result.raw_state||'尚未取得状态' }}</dd></div><div><dt>最近状态检查</dt><dd>{{ time(result.observed_at) }}</dd></div><div><dt>平台知识库</dt><dd>{{ result.collection_id||'尚未确认' }}</dd></div><div><dt>平台文档</dt><dd>{{ result.remote_id||'尚未收到回执' }}</dd></div><div><dt>文件版本</dt><dd>{{ result.version_id }}</dd></div></dl>
        <div class="job-preview-action"><button v-if="result.can_preview" class="secondary" :disabled="previewBusy&&previewReplica===result.id" @click="loadChunks(result)">{{ previewBusy&&previewReplica===result.id?'正在读取分块…':'查看实际分块' }}</button><small v-else>{{ result.state!=='ready'?'索引就绪后可查看分块正文。':'当前不可预览，请核对资料阅读权限和底座验收状态。' }}</small></div>
      </div>
      <p v-if="!detail.index_results?.length" class="muted">尚未建立索引记录；排队中的任务或不处理索引的维护任务可能没有此项。</p>
      <p class="muted">以上数量和状态来自最近保存的底座结果，不是处理百分比。点击“查看实际分块”实时读取该文件版本的正文分块，结果可能与上次保存的数量不同。</p>
      <section v-if="previewReplica" ref="previewPanel" class="job-chunk-preview" aria-labelledby="job-preview-title" :aria-busy="previewBusy">
        <div class="job-preview-heading"><div><h3 id="job-preview-title">实际分块预览</h3><small>{{ preview?.filename||detail.file?.filename }} · {{ providers[preview?.provider]||'知识底座' }}</small></div><button class="text-button" @click="resetPreview">收起预览</button></div>
        <p v-if="previewBusy" role="status">正在读取底座中的实际分块…</p>
        <div v-else-if="previewError" class="error" role="alert">{{ previewError }}<button class="text-button" @click="loadChunks(detail.index_results.find(r=>r.id===previewReplica),previewTarget)">重新读取</button></div>
        <template v-else-if="preview"><p class="job-preview-note">底座当前返回 {{ preview.total }} 个正文分块，第 {{ preview.page }} 页。本预览不包含图片附件或嵌套子分块；位置缺失时请对照原件核查。</p>
          <article v-for="(chunk,index) in preview.items" :key="chunk.id" class="job-chunk"><div class="job-chunk-heading"><strong>分块 {{ chunk.position??((preview.page-1)*preview.page_size+index+1) }}</strong><span>{{ chunkTypes[chunk.type]||chunk.type }}</span><span>{{ chunk.characters }} 字符</span><span v-if="chunk.enabled===false" class="job-error">已停用</span><span v-if="chunk.state">{{ backendStates[chunk.state]||chunk.state }}</span></div><small>{{ location(chunk) }}</small><pre class="job-chunk-content">{{ chunk.content }}</pre><p v-if="chunk.truncated" class="job-error">此分块超过预览长度，当前仅显示前 20000 字符，请到知识平台查看全文。</p><details><summary>分块标识</summary><small class="job-chunk-id">{{ chunk.id }}</small></details></article>
          <p v-if="!preview.items.length" class="muted">本页没有可返回的正文分块。</p>
          <div class="job-chunk-pagination"><span>第 {{ preview.page }} / {{ Math.max(1,Math.ceil(preview.total/preview.page_size)) }} 页</span><button class="secondary" :disabled="preview.page<=1" @click="loadChunks(detail.index_results.find(r=>r.id===previewReplica),preview.page-1)">上一页分块</button><button class="secondary" :disabled="!preview.has_more" @click="loadChunks(detail.index_results.find(r=>r.id===previewReplica),preview.page+1)">下一页分块</button></div>
        </template>
      </section>
    </AppDialog>
  </section>
</template>

<style scoped>
.jobs-page{max-width:1500px}.jobs-toolbar{display:flex;gap:18px;align-items:flex-end;flex-wrap:wrap;margin:24px 0}.jobs-toolbar label{font-size:12px}.jobs-toolbar input{width:280px;max-width:100%}.jobs-toolbar>.muted{margin-left:auto;padding-bottom:10px}.jobs-table{overflow-x:auto;border:1px solid #e2e9ed;border-radius:7px}.jobs-table table{min-width:850px}.jobs-table td{vertical-align:top}.jobs-table td:first-child{max-width:260px;overflow-wrap:anywhere}.jobs-table td:nth-child(2){max-width:300px}.jobs-table td:last-child{white-space:nowrap}.job-error{color:#a14f48;overflow-wrap:anywhere}.job-facts{margin:0 0 24px}.job-facts>div{display:grid;grid-template-columns:120px 1fr;gap:12px;padding:8px 0;border-bottom:1px solid #edf1f3}.job-facts dt{color:#839198;font-size:12px}.job-facts dd{margin:0;overflow-wrap:anywhere;font-size:12px}.job-index-result{border:1px solid #dce6eb;padding:16px;border-radius:6px;margin-bottom:12px}.job-index-heading{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:12px}.job-index-result .job-facts{margin-bottom:0}@media(max-width:650px){.jobs-toolbar{gap:12px}.jobs-toolbar>.muted{margin-left:0}.job-facts>div{grid-template-columns:90px 1fr}}
.job-preview-action{margin-top:16px}.job-chunk-preview{border-top:2px solid #dce7ec;margin-top:24px;padding-top:22px}.job-preview-heading{display:flex;justify-content:space-between;gap:12px;align-items:flex-start;margin-bottom:16px}.job-preview-heading h3{margin:0 0 5px;color:#315b67}.job-preview-note{font-size:12px;line-height:1.8;color:#728b95}.job-chunk{border:1px solid #dce7ec;border-radius:7px;padding:16px;margin:14px 0;background:#fff}.job-chunk-heading{display:flex;gap:12px;align-items:center;flex-wrap:wrap;font-size:11px;color:#728b95;margin-bottom:8px}.job-chunk-heading strong{font-size:14px;color:#315b67}.job-chunk-content{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit;line-height:1.85;color:#263a40;background:#f5f8fa;border-radius:5px;padding:14px;margin:14px 0}.job-chunk details{font-size:11px;color:#728b95}.job-chunk summary{cursor:pointer}.job-chunk-id{overflow-wrap:anywhere;margin-top:8px}.job-chunk-pagination{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin-top:18px}.job-chunk-pagination>span{margin-right:auto;font-size:12px;color:#728b95}
</style>
