<script setup lang="ts">
import { nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useJobsStore } from '../stores/jobs'
import { useSessionStore } from '../stores/session'
import { useWorkspace } from '../workspace'
import { kindLabels,stateLabels,updatedTime } from '../presentation'
import JobErrorDetails from '../components/JobErrorDetails.vue'
const jobsStore=useJobsStore(),session=useSessionStore(),workspace=useWorkspace()
const {jobs,loading,loadingMore,hasMore,initialized,loadError}=storeToRefs(jobsStore)
const {busy,activeJobs}=storeToRefs(session)
const scrollArea=ref<HTMLElement|null>(null),showBackToTop=ref(false)
const controlJob=workspace.controlJob
let resizeObserver: ResizeObserver | undefined, mounted=true
const progressPercent=(progress:number)=>Math.round(Math.max(0,Math.min(1,progress))*100)
function statusClass(state:string) {
  return ({succeeded:'badge-success',failed:'badge-error',running:'badge-info',paused:'badge-warning',pause_requested:'badge-warning'} as Record<string,string>)[state]||'badge-neutral'
}
function checkScroll() {
  const element=scrollArea.value
  if(!element || !mounted) return
  showBackToTop.value=element.scrollTop>240
  if(initialized.value && hasMore.value && !loading.value && !loadingMore.value && !loadError.value &&
    element.scrollHeight-element.scrollTop-element.clientHeight<240) void jobsStore.loadMore()
}
function backToTop() {
  scrollArea.value?.scrollTo({top:0,behavior:window.matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth'})
}
// Keep the visible row anchored when an event inserts newer jobs above it.
watch(jobs,async()=>{
  const element=scrollArea.value
  if(!element || element.scrollTop<50) return
  const top=element.getBoundingClientRect().top
  const anchor=Array.from(element.querySelectorAll<HTMLElement>('[data-job-id]')).find(row=>row.getBoundingClientRect().bottom>top)
  if(!anchor) return
  const id=anchor.dataset.jobId,offset=anchor.getBoundingClientRect().top
  await nextTick()
  const row=Array.from(element.querySelectorAll<HTMLElement>('[data-job-id]')).find(row=>row.dataset.jobId===id)
  if(mounted && row) element.scrollTop+=row.getBoundingClientRect().top-offset
})
watch([loading,loadingMore],async()=>{await nextTick();checkScroll()})
onMounted(()=>{
  void jobsStore.load()
  resizeObserver=new ResizeObserver(checkScroll)
  if(scrollArea.value) resizeObserver.observe(scrollArea.value)
})
onUnmounted(()=>{mounted=false;resizeObserver?.disconnect()})
</script>
<template>
  <section class="card card-border bg-base-100 w-full jobs-panel">
    <div class="jobs-heading">
      <div><h1>后台任务</h1><p>按创建时间倒序排列，向下滚动加载更多</p></div>
      <span class="badge badge-soft" :class="activeJobs?'badge-info':'badge-neutral'">{{ activeJobs }} 项进行中</span>
    </div>
    <div ref="scrollArea" class="jobs-scroll" tabindex="0" aria-label="后台任务列表" :aria-busy="loading || loadingMore" @scroll.passive="checkScroll">
      <div v-if="!jobs.length && !loadError" class="jobs-empty">{{ loading || !initialized?'正在加载后台任务…':'暂无后台任务' }}</div>
      <article v-for="job in jobs" :key="job.id" :data-job-id="job.id" class="jobs-entry">
        <div class="jobs-entry-copy">
          <div class="jobs-entry-title">
            <strong>{{ kindLabels[job.kind]||job.kind }}</strong>
            <span class="badge badge-soft badge-sm" :class="statusClass(job.state)">{{ stateLabels[job.state]||job.state }}</span>
          </div>
          <div class="jobs-entry-meta"><span :title="job.id">任务 {{ job.id.slice(-10) }}</span><time :datetime="new Date(job.created*1000).toISOString()">{{ updatedTime(job.created) }}</time></div>
          <div v-if="job.error" class="job-error-line"><JobErrorDetails :key="`${job.id}-${job.attempts}`" :job-id="job.id"/><p class="error-text">{{ job.error }}</p></div>
        </div>
        <div class="jobs-entry-progress">
          <span>进度 <strong>{{ progressPercent(job.progress) }}%</strong></span>
          <progress class="progress progress-primary" :value="progressPercent(job.progress)" max="100" :aria-label="`${kindLabels[job.kind]||job.kind}进度`"></progress>
        </div>
        <div class="jobs-entry-actions">
          <button v-if="['queued','running'].includes(job.state)" class="btn btn-sm" :disabled="busy" @click="controlJob(job,'pause')">暂停</button>
          <button v-if="job.state==='paused'" class="btn btn-sm" :disabled="busy" @click="controlJob(job,'resume')">恢复</button>
          <button v-if="job.state==='failed' && job.error?.includes('请求结果不确定')" class="btn btn-sm" :disabled="busy" title="确认已核查云端请求记录后，解除防重复提交保护" @click="controlJob(job,'acknowledge_asr_uncertain')">已核查云端，重试</button>
          <button v-else-if="job.state==='failed'" class="btn btn-sm" :disabled="busy" @click="controlJob(job,'retry')">重试</button>
          <button v-if="['queued','running','paused','pause_requested'].includes(job.state)" class="btn btn-ghost btn-sm" :disabled="busy" @click="controlJob(job,'cancel')">取消</button>
        </div>
      </article>
      <div v-if="loadError" class="jobs-load-state" role="alert"><p>{{ loadError }}</p><button class="btn btn-sm" :disabled="loading || loadingMore" @click="jobsStore.retry()">重试加载</button></div>
      <div v-else-if="jobs.length" class="jobs-load-state" role="status" aria-live="polite">
        <template v-if="loadingMore"><span class="loading loading-spinner loading-sm" aria-hidden="true"></span>正在加载更多任务…</template>
        <button v-else-if="hasMore" class="btn btn-ghost btn-sm" :disabled="loading" @click="jobsStore.loadMore()">加载更多</button>
        <span v-else>已加载全部 {{ jobs.length }} 项任务</span>
      </div>
    </div>
    <div class="jobs-footer">
      <span>已加载 {{ jobs.length }} 项任务 · 每次 20 条</span>
      <button v-if="showBackToTop" class="btn btn-sm" type="button" @click="backToTop">↑ 回到顶部</button>
    </div>
  </section>
</template>
