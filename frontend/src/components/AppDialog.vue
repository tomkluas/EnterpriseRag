<script setup>
import { onMounted, onUnmounted, nextTick, ref } from 'vue'
// 原生模态 dialog 负责焦点约束，业务内容和行为由插槽、事件接入。
const props=defineProps({title:String,description:String,busy:Boolean,submitLabel:{default:'保存'},readonly:Boolean,danger:Boolean,returnFocus:Object})
const emit=defineEmits(['close','submit'])
const dialog=ref(null)
let previousFocus=null
// 保存期间拦住取消键、关闭按钮和遮罩，避免请求结果失去归属。
function close(){if(!props.busy)emit('close')}
function backdrop(event){
  // dialog 自身也能接收内容边缘点击，不能只依赖事件目标判断用户是否点了遮罩。
  // self 点击也可能落在边框内，仅矩形外的点击视为遮罩。
  const rect=dialog.value.getBoundingClientRect()
  if(event.clientX<rect.left||event.clientX>rect.right||event.clientY<rect.top||event.clientY>rect.bottom)close()
}
// 记住触发按钮，卸载后等待 DOM 更新再恢复仍存在的焦点。
onMounted(()=>{previousFocus=props.returnFocus||document.activeElement;dialog.value.showModal()})
onUnmounted(()=>nextTick(()=>{if(previousFocus?.isConnected)previousFocus.focus()}))
</script>

<template>
  <!-- 标题与描述为辅助技术提供弹框名称，正文由业务页面填充。 -->
  <dialog ref="dialog" class="app-dialog" aria-labelledby="dialog-title" aria-describedby="dialog-description" @cancel.prevent="close" @click.self="backdrop">
    <form @submit.prevent="emit('submit')">
      <div class="dialog-heading"><div><h2 id="dialog-title">{{ title }}</h2><p id="dialog-description">{{ description }}</p></div><button type="button" class="dialog-close" aria-label="关闭弹框" :disabled="busy" @click="close">×</button></div>
      <div class="dialog-body"><slot /></div>
      <div class="dialog-footer"><button type="button" class="secondary" :disabled="busy" @click="close">{{ readonly?'关闭':'取消' }}</button><button v-if="!readonly" class="primary" :class="{'danger-primary':danger}" :disabled="busy">{{ busy?'正在保存…':submitLabel }}</button></div>
    </form>
  </dialog>
</template>
