import { createApp } from 'vue'
import App from './App.vue'
import './style.css'
import './chat-layout.css'
// 入口统一加载全局样式并挂载工作台，各业务页面自行维护状态。
createApp(App).mount('#app')
