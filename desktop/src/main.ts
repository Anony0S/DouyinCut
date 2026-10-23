import { createApp } from 'vue'
import { createPinia } from 'pinia'
import { createAppRouter } from './router'
import App from './App.vue'
import './style.css'
import 'cally'
import { startAppearance } from './appearance'

const stopAppearance = startAppearance()
if (import.meta.hot) import.meta.hot.dispose(stopAppearance)

const pinia=createPinia()
const router=createAppRouter(pinia)
createApp(App).use(pinia).use(router).mount('#app')
