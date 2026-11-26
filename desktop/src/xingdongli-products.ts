import { createApp } from 'vue'
import { startAppearance } from './appearance'
import XingdongliProductsView from './views/XingdongliProductsView.vue'
import './style.css'

const stopAppearance = startAppearance()
if (import.meta.hot) import.meta.hot.dispose(stopAppearance)

const monitorId = new URLSearchParams(window.location.search).get('monitor_id') || ''
createApp(XingdongliProductsView, { monitorId }).mount('#app')
