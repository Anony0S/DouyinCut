<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { RouterView, useRoute, useRouter } from 'vue-router'
import { useSessionStore } from './stores/session'
import { useProjectStore } from './stores/project'
import { createWorkspace, provideWorkspace } from './workspace'
import { provideVideoImport } from './composables/useVideoImport'
import LiveIcon from './components/live/LiveIcon.vue'
const route=useRoute(),router=useRouter(),session=useSessionStore(),project=useProjectStore()
const {busy,connected,activeJobs,error,message}=storeToRefs(session)
type ToastKind = 'success' | 'error'
type ToastMessage = { id: number; kind: ToastKind; text: string }
const toasts=ref<ToastMessage[]>([])
const toastTimers=new Map<number,number>()
let nextToastId=0
const workspace=createWorkspace(router)
provideWorkspace(workspace)
const {fileInput,selectFile}=provideVideoImport(workspace)
function dismissToast(id:number) {
  toasts.value=toasts.value.filter(item=>item.id!==id)
  const timer=toastTimers.get(id)
  if(timer!==undefined) window.clearTimeout(timer)
  toastTimers.delete(id)
}
function pushToast(kind:ToastKind,text:string) {
  if(toasts.value.length>=4) dismissToast(toasts.value[0].id)
  const id=++nextToastId
  toasts.value=[...toasts.value,{id,kind,text}]
  toastTimers.set(id,window.setTimeout(()=>dismissToast(id),kind==='error'?9000:6000))
}
watch(error,value=>{if(value) pushToast('error',value)})
watch(message,value=>{if(value) pushToast('success',value)})
onMounted(workspace.start)
onUnmounted(()=>{workspace.stop();for(const timer of toastTimers.values()) window.clearTimeout(timer);toastTimers.clear()})
</script>
<template>
  <div class="app-shell">
    <aside class="sidebar">
      <div class="brand"><span class="brand-icon">D</span><div>DouyinCut<small>服装直播剪辑工作台</small></div></div>
      <div class="nav-label">工作空间</div>
      <button :class="{active:route.name==='projects'}" :disabled="busy" @click="workspace.goProjects()">视频项目</button>
      <button :class="{active:route.name==='workspace'}" :disabled="!project.assetId || busy" @click="workspace.goEditor()">素材与粗剪 <span>01</span></button>
      <button :class="{active:route.name==='live-monitoring'}" :disabled="busy" @click="workspace.go('live-monitoring')">直播监控</button>
      <button :class="{active:route.name==='jobs'}" :disabled="busy" @click="workspace.go('jobs')">后台任务 <span>{{ activeJobs }}</span></button>
      <button :class="{active:route.name==='settings'}" :disabled="busy" @click="workspace.go('settings')">云端设置 <span>↗</span></button>
      <div class="sidebar-bottom"><span :class="['status-dot',{online:connected}]"></span>{{ connected?'本地服务已连接':'本地服务未连接' }}<p>v0.1</p></div>
    </aside>

    <main :class="{ 'workspace-main': route.name==='workspace', 'live-monitoring-main': route.name==='live-monitoring' }">
      <input ref="fileInput" class="file-input" type="file" accept="video/*,.mkv,.flv,.ts" @change="selectFile"/>
      <RouterView/>
    </main>
    <div class="toast toast-top toast-end app-toast-stack" aria-live="polite" aria-relevant="additions text">
      <div v-for="toast in toasts" :key="toast.id" class="alert app-toast" :class="`alert-${toast.kind}`" :role="toast.kind==='error'?'alert':'status'">
        <LiveIcon :name="toast.kind==='error'?'alert':'check'" />
        <span class="app-toast-message">{{ toast.text }}</span>
        <button class="btn btn-ghost btn-sm btn-square" type="button" aria-label="关闭提示" @click="dismissToast(toast.id)">
          <LiveIcon name="close" />
        </button>
      </div>
    </div>
  </div>
</template>
