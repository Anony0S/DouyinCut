<script setup lang="ts">
import { onMounted } from 'vue'
import { storeToRefs } from 'pinia'
import { useJobsStore } from '../stores/jobs'
import { useSessionStore } from '../stores/session'
import { useWorkspace } from '../workspace'
import { kindLabels,stateLabels } from '../presentation'
import JobErrorDetails from '../components/JobErrorDetails.vue'
const jobsStore=useJobsStore(),session=useSessionStore(),workspace=useWorkspace()
const {jobs}=storeToRefs(jobsStore)
const controlJob=workspace.controlJob
onMounted(()=>{void jobsStore.load().catch(session.report)})
</script>
<template><section class="panel"><div v-if="!jobs.length" class="empty">暂无后台任务</div><div v-for="job in jobs" :key="job.id" class="job-row"><div><strong>{{ kindLabels[job.kind]||job.kind }}</strong><small>{{ job.id.slice(-10) }} · {{ stateLabels[job.state]||job.state }}</small><div v-if="job.error" class="job-error-line"><JobErrorDetails :key="`${job.id}-${job.attempts}`" :job-id="job.id"/><p class="error-text">{{ job.error }}</p></div></div><progress :value="job.progress" max="1"></progress><div class="job-actions"><button v-if="['queued','running'].includes(job.state)" class="secondary" @click="controlJob(job,'pause')">暂停</button><button v-if="job.state==='paused'" class="secondary" @click="controlJob(job,'resume')">恢复</button><button v-if="job.state==='failed' && job.error?.includes('请求结果不确定')" class="secondary" title="确认已核查云端请求记录后，解除防重复提交保护" @click="controlJob(job,'acknowledge_asr_uncertain')">已核查云端，重试</button><button v-else-if="job.state==='failed'" class="secondary" @click="controlJob(job,'retry')">重试</button><button v-if="['queued','running','paused','pause_requested'].includes(job.state)" class="secondary" @click="controlJob(job,'cancel')">取消</button></div></div></section></template>
