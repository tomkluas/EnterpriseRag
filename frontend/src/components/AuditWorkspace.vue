<script setup>
import {computed,ref,onMounted,onUnmounted,nextTick} from 'vue'
import {api} from '../lib/api'
const search=ref(''),category=ref(''),page=ref(1),pageSize=ref(20)
const result=ref({items:[],total:0}),busy=ref(false),error=ref(''),table=ref(null)
const pages=computed(()=>Math.max(1,Math.ceil(result.value.total/pageSize.value)))
let controller=null,poller=null,disposed=false,request=0
// 自动刷新沿用已经提交的筛选条件，不在输入过程中触发新的搜索。
let applied={query:'',category:''}
const groups={document:'资料',space:'知识库',backend:'知识底座',job:'任务',conversation:'会话',message:'问答',trace:'问答审计',user:'成员与登录',role:'角色',organization:'组织',tenant:'企业',original:'原件维护'}
async function load(reset=false,target=page.value){
 if(reset){applied={query:search.value.trim(),category:category.value};target=1}
 controller?.abort();controller=new AbortController();const current=++request
 busy.value=true;error.value=''
 try{
  const params=new URLSearchParams({...applied,page:String(target),page_size:String(pageSize.value)})
  const data=await api('/audit?'+params,{signal:controller.signal})
  if(disposed||current!==request)return
  const changed=page.value!==data.page
  result.value=data;page.value=data.page
  if(changed){await nextTick();table.value?.scrollIntoView({block:'start'})}
 }catch(e){if(!disposed&&current===request&&e.name!=='AbortError'){error.value=e.message;result.value={items:[],total:0}}}
 finally{if(!disposed&&current===request)busy.value=false}
}
onMounted(()=>{load();poller=setInterval(()=>{if(page.value===1&&!busy.value)load()},10000)})
onUnmounted(()=>{disposed=true;controller?.abort();clearInterval(poller)})
</script>

<template>
 <section class="content-page audit-page"><div class="page-heading"><div><h1>操作记录</h1><p>查看谁在什么时候进行了哪项操作，最新记录排在前面。</p></div><button class="secondary" :disabled="busy" @click="load()">刷新记录</button></div>
  <form class="audit-filters" @submit.prevent="load(true)"><label>搜索记录<input v-model="search" type="search" :disabled="busy" placeholder="操作者、操作名称、文件名或资源编号"></label><label>操作类别<select v-model="category" :disabled="busy" @change="load(true)"><option value="">全部类别</option><option v-for="(name,key) in groups" :key="key" :value="key">{{ name }}</option></select></label><button class="secondary" :disabled="busy">查询</button><span class="muted">{{ page===1?'首页每 10 秒刷新':'正在查看历史记录，自动刷新已暂停' }}</span></form>
  <p class="audit-note">查询全部已记录的成功操作，按页加载。问答正文和处理耗时请到“问答审计”查看；未曾记录的历史操作不会补造。</p>
  <p v-if="error" class="error" role="alert">{{ error }}</p>
  <div ref="table" class="audit-table"><table :aria-busy="busy"><thead><tr><th>操作时间</th><th>操作者</th><th>操作内容</th><th>关联资源</th></tr></thead><tbody><tr v-for="a in result.items" :key="a.id"><td>{{ new Date(a.created_at*1000).toLocaleString() }}</td><td>{{ a.actor }}</td><td>{{ a.action_name||a.action }}<small>{{ a.action }}</small></td><td><strong>{{ a.resource_name||'资源编号' }}</strong><small class="audit-resource">{{ a.resource_id }}</small></td></tr></tbody></table></div>
  <div v-if="!result.items.length&&!error" class="empty-row">{{ busy?'正在加载操作记录…':'没有匹配的操作记录。' }}</div>
  <nav class="audit-pagination" aria-label="操作记录分页"><span aria-live="polite">共 {{ result.total }} 条，第 {{ page }} / {{ pages }} 页</span><div class="audit-pagination-controls"><label>每页 <select v-model.number="pageSize" :disabled="busy" @change="load(true)"><option :value="20">20 条</option><option :value="50">50 条</option><option :value="100">100 条</option></select></label><button class="secondary" :disabled="busy||page<=1" @click="load(false,page-1)">上一页</button><button class="secondary" :disabled="busy||page>=pages" @click="load(false,page+1)">下一页</button></div></nav>
 </section>
</template>

<style scoped>
.audit-pagination{display:flex;flex-direction:row;align-items:center;justify-content:flex-end;gap:14px;flex-wrap:wrap;margin-top:20px}.audit-pagination>span{margin-right:auto;color:#728b95;font-size:13px}.audit-pagination-controls{display:flex;align-items:center;gap:14px}.audit-pagination label{display:flex;flex-direction:row;align-items:center;gap:8px;font-size:12px;white-space:nowrap}.audit-pagination button{white-space:nowrap}.audit-pagination select{width:auto}.audit-table[aria-busy="true"]{opacity:.6}@media(max-width:650px){.audit-pagination>span{flex-basis:100%}.audit-pagination-controls{gap:8px}.audit-pagination button{padding:8px 10px}.audit-pagination select{padding:8px 10px}}
.audit-page{max-width:1500px}.audit-filters{display:flex;align-items:flex-end;gap:18px;flex-wrap:wrap;margin-top:24px}.audit-filters label{font-size:12px}.audit-filters input{width:340px;max-width:100%}.audit-filters>span{margin-left:auto;padding-bottom:10px}.audit-note{font-size:12px;margin:16px 0;color:#839198}.audit-table{overflow:auto;border:1px solid #e2e9ed;border-radius:7px}.audit-table table{min-width:760px}.audit-table td{vertical-align:top}.audit-table td:first-child{white-space:nowrap}.audit-table td:last-child{max-width:420px;overflow-wrap:anywhere}.audit-resource{user-select:all;font-family:monospace;font-size:11px}@media(max-width:650px){.audit-filters>span{margin-left:0}.audit-filters input{width:100%}.audit-filters label:first-child{width:100%}}
</style>
