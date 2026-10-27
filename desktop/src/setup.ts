import { createApp } from 'vue'
import WorkDirectorySetup from './views/WorkDirectorySetup.vue'
import './style.css'
import { startAppearance } from './appearance'
startAppearance()
createApp(WorkDirectorySetup).mount('#app')
