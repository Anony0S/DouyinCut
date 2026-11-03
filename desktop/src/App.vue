<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { RouterView, useRoute, useRouter } from 'vue-router'
import { useSessionStore } from './stores/session'
import { useProjectStore } from './stores/project'
import { createWorkspace, provideWorkspace } from './workspace'
import { provideVideoImport } from './composables/useVideoImport'
import LiveIcon from './components/live/LiveIcon.vue'
import { hasUnsavedChanges as hasRegisteredUnsavedChanges } from './exit-guards'
import { jobsApi } from './services/jobs'
const route=useRoute(),router=useRouter(),session=useSessionStore(),project=useProjectStore()
const {busy,connected,activeJobs,error,errorRevision,message}=storeToRefs(session)
const {savedDraft,planDirty}=storeToRefs(project)
const exitPrompt=ref<'drafts'|'activity'|null>(null), exiting=ref(false)
const exitRequestActive=ref(false), activeLocalJobs=ref(0), activeCloudJobs=ref(0)
const hasUnsavedChanges=computed(()=>busy.value || planDirty.value ||
  (Boolean(project.assetId) && savedDraft.value!==JSON.stringify(project.currentDraft())) ||
  hasRegisteredUnsavedChanges())
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
watch(errorRevision,()=>{if(error.value) pushToast('error',error.value)})
watch(message,value=>{if(value) pushToast('success',value)})
onMounted(workspace.start)
async function inspectExitRequest() {
  if(exitRequestActive.value || exiting.value) return
  exitRequestActive.value=true
  try {
    if(hasUnsavedChanges.value) {exitPrompt.value='drafts';return}
    await confirmActivityExit()
  } catch(cause) {
    session.report(cause)
    exitRequestActive.value=false
  }
}
async function confirmActivityExit() {
  const jobs=await jobsApi.list()
  const activeStates=['queued','running','pause_requested','cancel_requested']
  activeLocalJobs.value=jobs.filter(job=>['import','preview','export','jianying','live_product_clips'].includes(job.kind) &&
    activeStates.includes(job.state)).length
  activeCloudJobs.value=jobs.filter(job=>['analyze'].includes(job.kind) && activeStates.includes(job.state)).length
  if(activeLocalJobs.value || activeCloudJobs.value) {exitPrompt.value='activity';return}
  await beginExit()
}
async function beginExit() {
  exitPrompt.value=null
  exiting.value=true
  try {
    await nextTick()
    const tauri=(window as Window & {__TAURI_INTERNALS__?:{invoke:(command:string,args?:Record<string,unknown>)=>Promise<unknown>}}).__TAURI_INTERNALS__
    if(!tauri) throw new Error('无法连接桌面退出控制')
    await tauri.invoke('complete_exit')
  } catch(cause) {
    exiting.value=false
    exitRequestActive.value=false
    exitPrompt.value='activity'
    session.report(cause)
  }
}
function cancelExit() {
  exitPrompt.value=null
  exitRequestActive.value=false
}
function returnToSave() {
  exitPrompt.value=null
  exitRequestActive.value=false
}
function continueExit() {
  exitPrompt.value=null
  void confirmActivityExit().catch(cause=>{session.report(cause);cancelExit()})
}
function onExitRequest() { void inspectExitRequest() }
onMounted(()=>window.addEventListener('douyincut-exit-request',onExitRequest))
onUnmounted(()=>{window.removeEventListener('douyincut-exit-request',onExitRequest);workspace.stop();for(const timer of toastTimers.values()) window.clearTimeout(timer);toastTimers.clear()})
</script>
<template>
  <div class="app-shell">
    <aside class="sidebar">
      <div class="brand"><span class="brand-icon">D</span><div>DouyinCut<small>服装直播剪辑工作台</small></div></div>
      <div class="nav-label">工作空间</div>
      <button class="sidebar-link" :class="{active:route.name==='live-monitoring'}" :disabled="busy" @click="workspace.go('live-monitoring')">
        <LiveIcon name="radio"/><span class="nav-text">直播监控</span>
      </button>
      <button class="sidebar-link" :class="{active:route.name==='workspace'}" :disabled="!project.assetId || busy" @click="workspace.goEditor()">
        <LiveIcon name="video"/><span class="nav-text">素材与粗剪</span><span class="nav-badge">01</span>
      </button>
      <button class="sidebar-link" :class="{active:route.name==='projects'}" :disabled="busy" @click="workspace.goProjects()">
        <LiveIcon name="folder"/><span class="nav-text">视频项目</span>
      </button>
      <button class="sidebar-link" :class="{active:route.name==='jobs'}" :disabled="busy" @click="workspace.go('jobs')">
        <LiveIcon name="clock"/><span class="nav-text">后台任务</span><span class="nav-badge">{{ activeJobs }}</span>
      </button>
      <button class="sidebar-link" :class="{active:route.name==='appearance'}" :disabled="busy" @click="workspace.go('appearance')">
        <LiveIcon name="palette"/><span class="nav-text">外观</span>
      </button>
      <button class="sidebar-link" :class="{active:route.name==='settings'}" :disabled="busy" @click="workspace.go('settings')">
        <LiveIcon name="settings"/><span class="nav-text">云端设置</span>
      </button>
      <button class="sidebar-link" :class="{active:route.name==='local-settings'}" :disabled="busy" @click="workspace.go('local-settings')">
        <LiveIcon name="server"/><span class="nav-text">本地设置</span>
      </button>
      <div class="sidebar-bottom">
        <span
          class="connection-status"
          role="img"
          tabindex="0"
          :title="connected ? '本地服务已连接' : '本地服务未连接'"
          :aria-label="connected ? '本地服务已连接' : '本地服务未连接'">
          <LiveIcon name="server" :class="{online:connected}"/>
        </span>
        <span class="app-version">v0.2.99</span>
      </div>
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
    <div v-if="exitPrompt" class="modal modal-open exit-modal" role="presentation">
      <section class="modal-box" role="dialog" aria-modal="true" aria-labelledby="exit-title" aria-describedby="exit-description">
        <h2 id="exit-title">{{ exitPrompt==='drafts'?'有未保存的修改':activeCloudJobs?'退出并暂停当前任务？':'退出并暂停本地任务？' }}</h2>
        <p id="exit-description" v-if="exitPrompt==='drafts'">当前项目、片段方案或设置中有未保存内容。可以返回保存，或明确放弃这些修改后退出。</p>
        <template v-else>
          <p v-if="activeLocalJobs">当前有 {{ activeLocalJobs }} 项本地导入、预览或导出工作。退出后会在安全检查点暂停，重新打开应用后可手动恢复。</p>
          <p v-if="activeCloudJobs" id="exit-description">当前有 {{ activeCloudJobs }} 项后台分析任务。退出会中断本机等待，但不等于撤销已到达云端的请求；重新打开后会直接重发未完成阶段，可能重复执行并产生费用。</p>
          <p v-else id="exit-description">退出后本地任务会在安全检查点暂停，重新打开应用后可手动恢复。</p>
        </template>
        <div class="modal-action">
          <button v-if="exitPrompt==='drafts'" class="btn btn-primary" autofocus @click="returnToSave">返回保存</button>
          <button v-else class="btn" @click="cancelExit">继续使用</button>
          <button v-if="exitPrompt==='drafts'" class="btn btn-error" @click="continueExit">放弃并退出</button>
          <button v-else class="btn btn-error" @click="beginExit">退出并暂停</button>
        </div>
        <p v-if="exitPrompt==='drafts'" class="hint">选择“返回保存”不会自动保存或清除草稿。</p>
      </section>
    </div>
    <div v-if="exiting" class="modal modal-open exit-modal" role="status" aria-live="polite">
      <section class="modal-box exit-progress">
        <span class="loading loading-spinner loading-md" aria-hidden="true"></span>
        <h2>正在安全退出</h2>
        <p>正在暂停本地任务并关闭应用自己的后台进程。</p>
      </section>
    </div>
  </div>
</template>
