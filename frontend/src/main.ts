import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import 'element-plus/theme-chalk/dark/css-vars.css'
import App from './App.vue'
import './style.css'

const app = createApp(App)
app.use(createPinia())
app.use(ElementPlus)

// 深色模式：启动前从 localStorage 恢复，避免首屏闪烁
const savedTheme = localStorage.getItem('theme')
if (savedTheme === 'dark') {
  document.documentElement.classList.add('dark')
}

app.mount('#app')
