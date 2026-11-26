<script setup lang="ts">
import { computed, nextTick, onUnmounted, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useRoute, useRouter } from 'vue-router'
import { duration, seconds } from '../api'
import type { Plan } from '../api'
import { useProjectStore } from '../stores/project'
import { useSessionStore } from '../stores/session'
import { jobStateBadgeClass, kindLabels, stateLabels } from '../presentation'
import { useWorkspace } from '../workspace'
import { useVideoImport } from '../composables/useVideoImport'
import WorkspaceSplit from '../components/WorkspaceSplit.vue'
import JobErrorDetails from '../components/JobErrorDetails.vue'
import ConfirmPopover from '../components/ConfirmPopover.vue'
import CoverPromptPicker from '../components/CoverPromptPicker.vue'
import { projectsApi, type CoverReference } from '../services/projects'
import { productPlatformApi, isProductPlatformAuthError, isXingdongliAuthError } from '../services/productPlatform'
const project=useProjectStore(),session=useSessionStore(),workspace=useWorkspace(),route=useRoute(),router=useRouter()
const {busy,connected,pendingImportId}=storeToRefs(session)
const {assetId,activeAsset,productName,instructions,targetSeconds,toleranceSeconds,refreshAsr,analyzingAsset,
 latestAnalysis,transcript,transcriptVisible,assetJobs,selectedPlan,previewArtifact,assetPlans,planDirty}=storeToRefs(project)
const numberedPlans = computed(() => assetPlans.value.map((plan, index) => ({
  plan,
  number: assetPlans.value.length - index,
})))
const {analyze,loadTranscript,saveProjectInfo,savePlan,deletePlan,createJianyingDraft,moveClip,moveClipTo}=project
const filterShortFillers = ref(false)
const fillerMaxChars = ref<number | ''>(5)
const fillerMaxSeconds = ref<number | ''>(1)
watch(assetId, () => {
  filterShortFillers.value = false
  fillerMaxChars.value = 5
  fillerMaxSeconds.value = 1
})
function submitAnalysis() {
  if (!filterShortFillers.value) return analyze(null)
  if (fillerMaxChars.value === '' || fillerMaxSeconds.value === '') return
  return analyze({ max_chars: fillerMaxChars.value, max_duration_ms: Math.round(fillerMaxSeconds.value * 1000) })
}
const {chooseNewVideo}=useVideoImport()
const controlJob=workspace.controlJob
const productImages=ref<CoverReference[]>([]), selectedReferenceId=ref('')
const uploadedImages=ref<CoverReference[]>([])
const coverReferences=computed(()=>[
  ...uploadedImages.value.map(image=>({...image, uploaded:true})),
  ...productImages.value.map(image=>({...image, uploaded:false})),
])
const productImageStates=ref<Record<string,'loading'|'loaded'|'error'>>({})
const matchedProductTitle=ref('')
const coverImageKeyword=ref('')
const coverPrompt=ref('')
const showCoverPrompts=ref(false)
const coverError=ref(''), coverBusy=ref(false), loadingCoverImages=ref(false)
const platformImageError=ref(''), platformLoginRequired=ref<'xingdou' | 'xingdongli' | null>(null), uploadingCoverImage=ref(false)
const loadingUploadedImages=ref(false), deletingCoverImage=ref('')
const coverUploadInput=ref<HTMLInputElement|null>(null)
let coverDialogVersion=0
const coverDialog=ref<HTMLDialogElement|null>(null)
const originalDialog=ref<HTMLDialogElement|null>(null)
const originalState=ref<'loading'|'loaded'|'error'>('loading')
const originalJobId=ref('')
const coverPreviewState=ref<'loading'|'loaded'|'error'>('loading')
const matchingCoverJobs=computed(()=>assetJobs.value.filter(job=>job.kind==='cover'&&job.payload?.plan_id===selectedPlan.value?.id&&job.payload?.revision===selectedPlan.value?.revision).sort((a,b)=>b.created-a.created))
const successfulCoverJobs=computed(()=>matchingCoverJobs.value.filter(job=>job.state==='succeeded'&&typeof job.result?.cover_id==='string'))
const selectedCoverJob=computed(()=>successfulCoverJobs.value[0]||null)
const activeCoverJob=computed(()=>matchingCoverJobs.value.find(job=>['queued','running','pause_requested','paused','cancel_requested'].includes(job.state))||null)
watch(()=>selectedCoverJob.value?.id,()=>{coverPreviewState.value='loading'})
function coverMediaUrl(jobId:string) { return `/api/v1/assets/${encodeURIComponent(assetId.value)}/covers/${encodeURIComponent(jobId)}/media` }
function originalMediaUrl(jobId:string) { return `/api/v1/assets/${encodeURIComponent(assetId.value)}/covers/${encodeURIComponent(jobId)}/original` }
async function openOriginal() { originalState.value='loading'; originalJobId.value=selectedCoverJob.value?.id||''; await nextTick(); originalDialog.value?.showModal() }
function closeOriginal() { originalDialog.value?.close(); originalJobId.value='' }
function markCoverPreviewLoaded() { coverPreviewState.value='loaded' }
function markCoverPreviewFailed() { coverPreviewState.value='error' }
const canGenerateCover=computed(()=>Boolean(connected.value&&selectedPlan.value&&activeAsset.value&&coverPrompt.value.trim()&&
  productImageStates.value[selectedReferenceId.value]==='loaded'&&
  !planDirty.value&&!activeCoverJob.value&&!coverBusy.value&&!uploadingCoverImage.value&&!deletingCoverImage.value))
function handleCoverPlatformError(error:unknown, message:string) {
  platformImageError.value=error instanceof Error?error.message:message
  platformLoginRequired.value=isProductPlatformAuthError(error) ? 'xingdou' : isXingdongliAuthError(error) ? 'xingdongli' : null
}
async function openCoverDraftDialog() {
  const plan=selectedPlan.value
  const asset=activeAsset.value
  if(!plan||planDirty.value||!asset||!connected.value||coverBusy.value) return
  const version=++coverDialogVersion
  coverError.value=''; matchedProductTitle.value=''; productImages.value=[]; selectedReferenceId.value=''
  platformImageError.value=''; platformLoginRequired.value=null; loadingCoverImages.value=false
  uploadedImages.value=[]; productImageStates.value={}; uploadingCoverImage.value=false
  deletingCoverImage.value=''; loadingUploadedImages.value=true
  const original=productName.value.trim()
  coverImageKeyword.value=original
  coverPrompt.value=''
  showCoverPrompts.value=true
  await nextTick()
  if(version!==coverDialogVersion) return
  coverDialog.value?.showModal()
  await Promise.all([loadUploadedCoverImages(asset.id,version),loadCoverImages(false)])
}
function closeCoverDraftDialog() {
  if(coverBusy.value) return
  coverDialogVersion++
  coverDialog.value?.close()
  showCoverPrompts.value=false
}
watch([assetId,()=>selectedPlan.value?.id,()=>selectedPlan.value?.revision],()=>{
  coverDialogVersion++
  if(coverDialog.value?.open) coverDialog.value.close()
  showCoverPrompts.value=false
},{flush:'sync'})
async function loadCoverImages(refresh=false) {
  const keyword=coverImageKeyword.value.trim()
  const currentAsset=assetId.value
  if(!keyword||!currentAsset||loadingCoverImages.value) return
  const version=coverDialogVersion
  platformImageError.value=''; platformLoginRequired.value=null; loadingCoverImages.value=true
  matchedProductTitle.value=''
  for(const image of productImages.value) delete productImageStates.value[image.id]
  productImages.value=[]
  selectAvailableCoverReference()
  try {
    const match=await productPlatformApi.searchCoverProduct(currentAsset,keyword,refresh)
    if(version!==coverDialogVersion) return
    if(!match.product?.id) {platformImageError.value='平台未查询到可取图的商品';return}
    const images=match.reference_images ?? (await productPlatformApi.images(currentAsset,match.product.id,refresh)).images
    if(version!==coverDialogVersion) return
    matchedProductTitle.value=match.product.title||''
    productImages.value=images
    selectAvailableCoverReference()
    if(!images.length) platformImageError.value='该商品没有可用图片'
  } catch(error) {
    if(version===coverDialogVersion) handleCoverPlatformError(error,'获取商品图片失败，请重试')
  } finally {
    if(version===coverDialogVersion) loadingCoverImages.value=false
  }
}
function selectAvailableCoverReference() {
  const usable=coverReferences.value.filter(image=>productImageStates.value[image.id]!=='error')
  if(!usable.some(image=>image.id===selectedReferenceId.value)) selectedReferenceId.value=usable[0]?.id||''
}
async function loadUploadedCoverImages(currentAsset:string,version:number) {
  try {
    const result=await projectsApi.uploadedCoverReferences(currentAsset)
    if(version!==coverDialogVersion) return
    uploadedImages.value=result.images
    selectAvailableCoverReference()
  } catch(error) {
    if(version===coverDialogVersion) coverError.value=error instanceof Error?error.message:'无法读取已上传的图片'
  } finally {
    if(version===coverDialogVersion) loadingUploadedImages.value=false
  }
}
async function uploadCoverImage(event:Event) {
  const input=event.target as HTMLInputElement
  const file=input.files?.[0]
  input.value=''
  if(!file||!assetId.value||uploadingCoverImage.value||loadingUploadedImages.value||deletingCoverImage.value||coverBusy.value) return
  if(file.size>20*1024*1024) {coverError.value='参考图片不能超过 20 MB';return}
  const version=coverDialogVersion
  uploadingCoverImage.value=true
  coverError.value=''
  try {
    const image=await projectsApi.uploadCoverReference(assetId.value,file)
    if(version!==coverDialogVersion) return
    if(!uploadedImages.value.some(item=>item.id===image.id)) uploadedImages.value.unshift(image)
    selectedReferenceId.value=image.id
  } catch(error) {
    if(version===coverDialogVersion) coverError.value=error instanceof Error?error.message:'上传参考图失败，请重试'
  } finally {
    if(version===coverDialogVersion) uploadingCoverImage.value=false
  }
}
async function deleteCoverImage(imageId:string) {
  if(deletingCoverImage.value||coverBusy.value||uploadingCoverImage.value) return
  const version=coverDialogVersion
  deletingCoverImage.value=imageId
  coverError.value=''
  try {
    await projectsApi.deleteCoverReference(assetId.value,imageId)
    if(version!==coverDialogVersion) return
    uploadedImages.value=uploadedImages.value.filter(image=>image.id!==imageId)
    delete productImageStates.value[imageId]
    selectAvailableCoverReference()
  } catch(error) {
    if(version===coverDialogVersion) coverError.value=error instanceof Error?error.message:'删除图片失败，请重试'
  } finally {
    if(version===coverDialogVersion) deletingCoverImage.value=''
  }
}
function markProductImageLoaded(id:string) { productImageStates.value[id]='loaded' }
function markProductImageFailed(id:string) {
  productImageStates.value[id]='error'
  if(selectedReferenceId.value===id) {
    selectAvailableCoverReference()
    if(uploadedImages.value.some(image=>image.id===id)) coverError.value='上传的图片无法打开，请换一张参考图'
  }
}
async function generateCover() {
  const plan=selectedPlan.value
  if(!plan||!activeAsset.value||!canGenerateCover.value) return
  coverBusy.value=true;coverError.value=''
  try {
    const name = productName.value.trim()
    const prompt = name ? `${coverPrompt.value.trim()}\n商品名称：${name}` : coverPrompt.value.trim()
    await projectsApi.generateCover(activeAsset.value.id,plan.id,plan.revision,selectedReferenceId.value,prompt)
    session.message='封面生成任务已排队，可在当前项目任务状态查看进度'
    await project.refresh()
  } catch(error) { coverError.value=error instanceof Error?error.message:'封面任务提交失败，请重试' }
  finally { coverBusy.value=false }
}
async function createCoveredDraft() {
  const plan=selectedPlan.value
  if(!plan||!selectedCoverJob.value||planDirty.value) return
  await createJianyingDraft(plan,selectedCoverJob.value.id)
  closeCoverDraftDialog()
}
const video=ref<HTMLVideoElement|null>(null),mediaUrl=ref('')
let cleanupPlayback: (()=>void) | undefined
async function toggleTranscript() {
  if (transcriptVisible.value) {
    transcriptVisible.value = false
    return
  }
  if (transcript.value) {
    transcriptVisible.value = true
    return
  }
  await loadTranscript()
}
function stopSourcePlayback() {cleanupPlayback?.();cleanupPlayback=undefined;video.value?.pause()}
async function playClip(index: number) {
  const clip=selectedPlan.value?.clips[index]
  if(!clip) return
  stopSourcePlayback()
  mediaUrl.value=`/api/v1/assets/${clip.source_asset_id}/media`
  await nextTick()
  const audioStartUs=activeAsset.value?.audio_start_us??0
  const mediaStartUs=Math.min(audioStartUs,activeAsset.value?.video_start_us??0)
  const clipStartSeconds=Math.max(0,(clip.source_start_us+audioStartUs-mediaStartUs)/1e6)
  const clipEndSeconds=Math.max(clipStartSeconds,(clip.source_end_us+audioStartUs-mediaStartUs)/1e6)
  const seek=()=>{if(video.value){video.value.currentTime=clipStartSeconds;void video.value.play().catch(()=>{})}}
  const stop=()=>{if(video.value&&video.value.currentTime>=clipEndSeconds){video.value.pause();stopSourcePlayback()}}
  cleanupPlayback=()=>{video.value?.removeEventListener('timeupdate',stop);video.value?.removeEventListener('loadedmetadata',seek)}
  video.value?.addEventListener('timeupdate',stop)
  if(video.value?.readyState&&video.value.currentSrc.endsWith(`/assets/${clip.source_asset_id}/media`)) seek()
  else video.value?.addEventListener('loadedmetadata',seek,{once:true})
}

function clipEvidence(id: string) {return project.candidates.find(c=>c.id===id)}
function selectPlan(plan: Plan) {
  if(plan.id===selectedPlan.value?.id) return
  if(planDirty.value&&!window.confirm('当前方案有未保存的片段调整，切换后将丢失这些调整。继续切换吗？')) return
  selectedPlan.value=JSON.parse(JSON.stringify(plan))
}
function planDeleteTitle(plan: Plan, number: number) {
  return `删除方案 ${number}（修订 ${plan.revision}）？`
}
function planDeleteMessage(plan: Plan) {
  const dirty=planDirty.value&&selectedPlan.value?.id===plan.id
  return `该方案的本地预览和导出文件会一并删除。源视频、ASR 对话及其他方案会保留。${dirty?'当前未保存的片段调整也会丢失。':''}此操作不可撤销。`
}
function removeClip(index: number) {
  if(!selectedPlan.value || selectedPlan.value.clips.length===1) return
  selectedPlan.value.clips.splice(index,1)
}
function clipRemoveMessage(index: number) {
  return `将从当前方案草稿中移除片段 ${index+1}。这是未保存的调整，保存方案后才会生效。`
}
// Reordering is driven by pointer events on a handle: the desktop WebView reserves native
// drag-and-drop for file drops, so HTML drag events do not arrive there.
const clipList=ref<HTMLElement|null>(null)
const clipElements: (HTMLElement|null)[]=[]
const clipDrag=ref<{from:number;slot:number}|null>(null)
let dragPointer: number|null=null,dragHandle: HTMLElement|null=null,dragY=0
let dragScroll: ReturnType<typeof setInterval>|undefined
// The slot is an insertion point; dropping beside the dragged clip itself changes nothing.
const dropSlot=computed(()=>clipDrag.value&&clipDrag.value.slot!==clipDrag.value.from&&clipDrag.value.slot!==clipDrag.value.from+1?clipDrag.value.slot:-1)
function setClipElement(index: number,element: unknown) {clipElements[index]=element as HTMLElement|null}
function slotAtPointer() {
  const count=selectedPlan.value?.clips.length??0
  let slot=0
  for(let index=0;index<count;index++) {
    const rect=clipElements[index]?.getBoundingClientRect()
    if(rect&&dragY>rect.top+rect.height/2) slot=index+1
  }
  return slot
}
function startClipDrag(index: number,event: PointerEvent) {
  if(event.button!==0||dragPointer!==null||busy.value||(selectedPlan.value?.clips.length??0)<2) return
  dragPointer=event.pointerId;dragHandle=event.currentTarget as HTMLElement;dragY=event.clientY
  // Capture keeps the drag alive outside the handle; a failed capture must not leave a drag half-started.
  try {dragHandle.setPointerCapture(dragPointer)} catch {dragPointer=null;dragHandle=null;return}
  clipDrag.value={from:index,slot:index}
  dragScroll=setInterval(scrollClipListWhileDragging,16)
  window.addEventListener('keydown',cancelClipDragOnEscape)
  event.preventDefault()
}
function moveClipDrag(event: PointerEvent) {
  if(event.pointerId!==dragPointer||!clipDrag.value) return
  dragY=event.clientY;clipDrag.value.slot=slotAtPointer()
}
function scrollClipListWhileDragging() {
  const list=clipList.value
  if(!list||!clipDrag.value) return
  const rect=list.getBoundingClientRect()
  const step=dragY<rect.top+48?-12:dragY>rect.bottom-48?12:0
  if(step) {list.scrollTop+=step;clipDrag.value.slot=slotAtPointer()}
}
function finishClipDrag(commit: boolean) {
  if(dragPointer===null) return
  const drag=clipDrag.value,pointer=dragPointer
  clipDrag.value=null;dragPointer=null
  clearInterval(dragScroll);dragScroll=undefined
  window.removeEventListener('keydown',cancelClipDragOnEscape)
  if(dragHandle?.hasPointerCapture(pointer)) dragHandle.releasePointerCapture(pointer)
  dragHandle=null
  if(commit&&drag) moveClipTo(drag.from,drag.slot)
}
function endClipDrag(event: PointerEvent) {if(event.pointerId===dragPointer) finishClipDrag(event.type==='pointerup')}
function cancelClipDragOnEscape(event: KeyboardEvent) {if(event.key==='Escape') finishClipDrag(false)}
function moveClipWithKey(index: number,event: KeyboardEvent) {
  const delta=event.key==='ArrowUp'?-1:event.key==='ArrowDown'?1:0
  if(!delta) return
  event.preventDefault();moveClip(index,delta)
}
watch(assetId,id=>{stopSourcePlayback();mediaUrl.value=id ? `/api/v1/assets/${id}/media` : ''},{immediate:true})
watch(()=>selectedPlan.value?.id,()=>finishClipDrag(false))
onUnmounted(()=>{coverDialogVersion++;stopSourcePlayback();finishClipDrag(false)})
</script>
<template><template v-if="activeAsset?.id===route.params.id">
        <div class="workspace-project-bar"><button class="secondary" :disabled="busy" @click="workspace.goProjects()">← 返回视频项目</button><div class="workspace-project-name"><strong :title="activeAsset.name">{{ activeAsset.name }}</strong><small>{{ duration(activeAsset.duration_us) }} · {{ activeAsset.width }} × {{ activeAsset.height }}</small></div><button class="secondary" :disabled="busy||!connected||Boolean(pendingImportId)" @click="chooseNewVideo">{{ pendingImportId?'正在导入…':'新建视频项目' }}</button><button class="secondary" :disabled="busy" @click="saveProjectInfo">保存项目设置</button></div>
        <WorkspaceSplit>
          <template #left>
          <section class="panel roughcut-controls">
            <div class="roughcut-config-group"><div class="panel-title"><h2>01 / 商品资料与要求</h2></div>
            <form @submit.prevent="submitAnalysis">
              <label for="product-name">商品名称</label><input id="product-name" v-model="productName" required maxlength="200" placeholder="独束分身型微定制【奶黄千层】假两件针织开衫"/>
              <div class="mt-4 flex flex-wrap items-center justify-between gap-2">
                <label for="roughcut-instructions" class="m-0">剪辑要求</label>
                <button type="button" class="btn btn-sm" :disabled="busy||!connected" @click="project.saveDefaultInstructions()">保存为默认</button>
              </div>
              <textarea id="roughcut-instructions" v-model="instructions" class="textarea mt-2 w-full" maxlength="4000" rows="8" aria-describedby="instructions-save-hint"/>
              <p id="instructions-save-hint" class="hint">保存后用于后续新项目；当前项目的要求可单独修改，分析失败不会丢失已保存内容。</p>
              <label for="target-duration">目标时长</label><select id="target-duration" v-model.number="targetSeconds"><option :value="60">约 60 秒</option><option :value="90">约 90 秒</option></select>
              <label for="duration-tolerance" class="flex flex-wrap items-center justify-between gap-2">允许偏差 <output for="duration-tolerance">± {{ toleranceSeconds }} 秒</output></label>
              <input id="duration-tolerance" v-model.number="toleranceSeconds" class="range range-sm mt-2 w-full text-primary [--range-bg:var(--color-base-300)] [--range-thumb:var(--color-primary)] [--range-fill:0]" type="range" min="0" max="30" step="1" :disabled="busy||analyzingAsset" :aria-valuetext="'允许偏差 ' + toleranceSeconds + ' 秒'" aria-describedby="duration-tolerance-hint"/>
              <div class="mt-1 flex justify-between text-xs text-base-content/60" aria-hidden="true"><span>0 秒</span><span>15 秒</span><span>30 秒</span></div>
              <p id="duration-tolerance-hint" class="hint">优先控制在 {{ Math.max(0,targetSeconds-toleranceSeconds) }}～{{ targetSeconds+toleranceSeconds }} 秒；素材不足可更短，最长不超过 {{ targetSeconds+toleranceSeconds }} 秒。</p>
              <div class="mt-3 flex items-center gap-1.5 whitespace-nowrap sm:gap-2">
                <label class="m-0! flex shrink-0 cursor-pointer items-center gap-1">
                  <input v-model="filterShortFillers" class="checkbox checkbox-xs" type="checkbox" :disabled="busy||analyzingAsset" />
                  过滤短语气词
                </label>
                <template v-if="filterShortFillers">
                  <label class="m-0! flex min-w-0 items-center gap-1">
                    <input v-model.number="fillerMaxChars" class="input input-xs w-10 min-w-0 px-0.5! py-0! text-center [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none" type="number" min="1" step="1" required :disabled="busy||analyzingAsset" aria-label="少于多少字" />
                    字
                  </label>
                  <label class="m-0! flex min-w-0 items-center gap-1">
                    <input v-model.number="fillerMaxSeconds" class="input input-xs w-10 min-w-0 px-0.5! py-0! text-center [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none" type="number" min="0.1" step="0.1" required :disabled="busy||analyzingAsset" aria-label="时长小于多少秒" />
                    秒
                  </label>
                </template>
              </div>
              <label class="asr-refresh"><input v-model="refreshAsr" class="checkbox checkbox-xs" type="checkbox"/>重新识别语音（会再次调用 ASR）</label>
              <button class="primary full" :disabled="busy||!connected||!activeAsset||!productName.trim()||analyzingAsset">{{ analyzingAsset?'正在分析与粗剪…':'使用云端 AI 分析并粗剪' }}</button>
            </form>
            <p class="hint">每 2 分钟上传识别，完成整个视频的带时间戳对话后统一决策，按选中的时间区间裁剪并拼接。</p>
            <button v-if="latestAnalysis" class="secondary" :disabled="busy" :aria-expanded="transcriptVisible" @click="toggleTranscript">{{ transcriptVisible?'收起全量 ASR 对话与时间戳':'查看全量 ASR 对话与时间戳' }}</button>
            <div v-if="transcriptVisible" class="transcript-list"><p v-if="!transcript?.ready" class="hint">全量转写尚未完成，请稍后再次查看。</p><template v-else><p class="hint">共 {{ transcript.sentences.length }} 条口播。</p><p v-for="(line,index) in transcript.sentences" :key="index"><strong>{{ seconds(line.start_us) }}～{{ seconds(line.end_us) }} 秒</strong><br/>{{ line.text }}</p></template></div>
            </div>
            <section v-if="assetJobs.length" class="asset-task-list" aria-label="当前项目任务">
            <h2>任务状态</h2>
            <div class="asset-task-rows">
              <div v-for="job in assetJobs" :key="job.id" class="roughcut-job"
                :class="{ 'is-finished': ['succeeded', 'cancelled'].includes(job.state) }">
                <div class="flex flex-wrap items-center gap-x-2 gap-y-1">
                  <small>{{ kindLabels[job.kind] || job.kind }}</small>
                  <span class="badge badge-soft badge-xs roughcut-job-status" :class="jobStateBadgeClass(job.state)">
                    {{ stateLabels[job.state] || job.state }}
                  </span>
                </div>
                <progress v-if="!['succeeded', 'cancelled'].includes(job.state)" class="progress progress-primary roughcut-job-progress"
                  :value="job.progress" max="1" :aria-label="`${kindLabels[job.kind] || job.kind}进度`" />
                <div v-if="job.kind === 'jianying' && job.result?.draft_path" class="jianying-job-path">
                  <strong>剪映草稿目录</strong>
                  <code>{{ job.result.draft_path }}</code>
                  <small>视频片段 {{ job.result.clip_count }} · 字幕 {{ job.result.subtitle_count }}</small>
                </div>
                <div v-if="job.error" class="job-error-line">
                  <JobErrorDetails :key="`${job.id}-${job.attempts}`" :job-id="job.id" />
                  <p class="error-text">{{ job.error }}</p>
                </div>
                <button v-if="job.state === 'failed' && job.error?.includes('请求结果不确定')" class="secondary"
                  title="确认已核查云端请求记录后，解除防重复提交保护" @click="controlJob(job, 'acknowledge_asr_uncertain')">已核查云端，重试</button>
                <button v-else-if="job.state === 'failed'" class="secondary" @click="controlJob(job, 'retry')">重试任务</button>
              </div>
            </div>
            </section>
          </section>
          </template>
          <template #right>
          <section class="panel roughcut-preview">
            <div class="panel-title workspace-result-heading"><h2>02 / 预览与调整</h2><span v-if="selectedPlan">版本 {{ selectedPlan.revision }}</span></div>
            <div class="roughcut-editing-grid">
              <div class="roughcut-viewer">
                <div class="roughcut-players">
                <section class="source-preview">
                  <div class="preview-heading"><h3>原素材 / 源片段</h3><button v-if="activeAsset" type="button" class="preview-heading-action" title="从头播放完整原素材" @click="mediaUrl=`/api/v1/assets/${assetId}/media`;stopSourcePlayback()">播放全片</button></div>
                  <div class="video-stage"><video v-if="mediaUrl" ref="video" :src="mediaUrl" controls preload="metadata"/><div v-else class="empty">选择视频开始</div></div>
                </section>
                <section class="joined-preview">
                  <div class="preview-heading"><h3>拼接成片</h3><span v-if="selectedPlan">{{ selectedPlan.clips.length }} 段 · {{ seconds(selectedPlan.clips.reduce((sum,c)=>sum+c.source_end_us-c.source_start_us,0)) }} 秒</span></div>
                  <template v-if="selectedPlan">
                    <video v-if="previewArtifact" :key="previewArtifact.id" class="roughcut-result-video" :src="`/api/v1/exports/${previewArtifact.id}/media`" controls preload="metadata"/>
                    <div v-else class="empty preview-pending">正在生成当前版本的拼接预览<br/>请留意任务状态</div>
                  </template>
                  <div v-else class="empty preview-pending">完成语音分析与粗剪后<br/>在这里查看拼接成片</div>
                </section>
                </div>
                <div v-if="selectedPlan?.warnings.length" class="preview-warnings"><p v-for="warning in selectedPlan.warnings" :key="warning" class="hint">{{ warning }}</p></div>
                <details v-if="numberedPlans.length" class="saved-plans" open>
                  <summary>粗剪方案 · {{ numberedPlans.length }}</summary>
                  <div class="saved-plan-list">
                    <div v-for="{ plan, number } in numberedPlans" :key="plan.id" class="saved-plan-row">
                      <button class="secondary saved-plan" :class="{ 'saved-plan-selected': plan.id === selectedPlan?.id }"
                        :aria-pressed="plan.id === selectedPlan?.id" :disabled="busy" @click="selectPlan(plan)">
                        方案 {{ number }} · {{ seconds(plan.clips.reduce((sum, clip) => sum + clip.source_end_us - clip.source_start_us, 0)) }} 秒 · 修订 {{ plan.revision }}
                      </button>
                      <ConfirmPopover :title="planDeleteTitle(plan, number)" :message="planDeleteMessage(plan)"
                        :confirm-action="() => deletePlan(plan.id)" :disabled="busy" v-slot="{ open, expanded, popoverId }">
                        <button class="danger-button saved-plan-delete" :aria-label="`删除方案 ${number}`" title="删除方案"
                          :disabled="busy" :aria-expanded="expanded" :aria-controls="popoverId" aria-haspopup="dialog" @click="open">删除</button>
                      </ConfirmPopover>
                    </div>
                  </div>
                </details>
              </div>
              <div class="roughcut-editor">
                <div class="clip-list-heading"><h3>入选片段 <span v-if="selectedPlan">{{ selectedPlan.clips.length }}</span></h3><span>从上到下依次拼接 · 可拖动排序</span></div>
                <template v-if="selectedPlan">
                  <div ref="clipList" class="roughcut-clip-list" :class="{'is-reordering':clipDrag}">
                    <article v-for="(clip,index) in selectedPlan.clips" :key="`${selectedPlan.id}:${clip.candidate_id}`" :ref="element => setClipElement(index,element)" class="roughcut-clip" :class="{'is-dragging':clipDrag?.from===index,'drop-before':dropSlot===index,'drop-after':dropSlot===selectedPlan.clips.length&&index===selectedPlan.clips.length-1}">
                      <div class="clip-heading"><div class="clip-identity"><button type="button" class="clip-drag-handle" :disabled="busy||selectedPlan.clips.length<2" :aria-label="`拖动片段 ${index+1} 调整顺序`" title="拖动调整顺序；聚焦后可用上下方向键移动" @pointerdown="startClipDrag(index,$event)" @pointermove="moveClipDrag" @pointerup="endClipDrag" @pointercancel="endClipDrag" @lostpointercapture="endClipDrag" @keydown="moveClipWithKey(index,$event)"><svg viewBox="0 0 10 16" width="10" height="16" fill="currentColor" aria-hidden="true"><circle cx="2.5" cy="3" r="1.3"/><circle cx="7.5" cy="3" r="1.3"/><circle cx="2.5" cy="8" r="1.3"/><circle cx="7.5" cy="8" r="1.3"/><circle cx="2.5" cy="13" r="1.3"/><circle cx="7.5" cy="13" r="1.3"/></svg></button><span class="clip-number">{{ String(index+1).padStart(2,'0') }}</span><div><h3>片段 {{ index+1 }}</h3><span class="clip-source-time">{{ seconds(clip.source_start_us) }}–{{ seconds(clip.source_end_us) }} 秒 <span>· {{ seconds(clip.source_end_us-clip.source_start_us) }} 秒</span></span></div></div><button class="secondary clip-play" @click="playClip(index)">▶ 源片段</button></div>
                      <details class="clip-dialogue"><summary><span class="clip-dialogue-excerpt">{{ clipEvidence(clip.candidate_id)?.evidence || '暂无口播文本' }}</span><span class="clip-dialogue-toggle">展开</span></summary><p class="transcript-evidence">{{ clipEvidence(clip.candidate_id)?.evidence }}</p></details>
                      <div class="clip-edit-row"><div class="clip-time-fields"><label :for="`clip-start-${clip.candidate_id}`" title="开始时间，单位：秒">开始<input :id="`clip-start-${clip.candidate_id}`" :value="clip.source_start_us/1e6" type="number" step="0.1" @change="clip.source_start_us=Math.round(Number(($event.target as HTMLInputElement).value)*1e6)"/></label><label :for="`clip-end-${clip.candidate_id}`" title="结束时间，单位：秒">结束<input :id="`clip-end-${clip.candidate_id}`" :value="clip.source_end_us/1e6" type="number" step="0.1" @change="clip.source_end_us=Math.round(Number(($event.target as HTMLInputElement).value)*1e6)"/></label></div><div class="roughcut-clip-actions"><button class="secondary" :disabled="index===0" :aria-label="`上移片段 ${index+1}`" title="上移" @click="moveClip(index,-1)">↑</button><button class="secondary" :disabled="index===selectedPlan.clips.length-1" :aria-label="`下移片段 ${index+1}`" title="下移" @click="moveClip(index,1)">↓</button><ConfirmPopover :title="`移除片段 ${index+1}？`" :message="clipRemoveMessage(index)" confirm-label="确定移除" :confirm-action="() => removeClip(index)" :disabled="busy || selectedPlan.clips.length===1" v-slot="{ open, expanded, popoverId }"><button class="secondary clip-remove" :disabled="busy || selectedPlan.clips.length===1" :aria-label="`移除片段 ${index+1}`" :aria-expanded="expanded" :aria-controls="popoverId" aria-haspopup="dialog" @click="open">移除</button></ConfirmPopover></div></div>
                    </article>
                  </div>
                  <div class="roughcut-editor-footer"><p class="hint" :class="{'unsaved-hint':planDirty}">{{ planDirty?'有未保存的调整，保存后会重新生成预览。':'可调整切点、顺序，或移除不需要的片段。' }}</p><div class="roughcut-save-actions"><button class="secondary" :disabled="busy||!selectedPlan.clips.length" @click="savePlan">保存并重新预览</button><button class="secondary" :disabled="busy||planDirty||!selectedPlan.clips.length" @click="createJianyingDraft(selectedPlan)">生成剪映草稿</button><button class="primary" :disabled="busy||planDirty||!selectedPlan.clips.length||!connected" @click="openCoverDraftDialog">生成带封面剪映草稿</button></div></div>
                </template>
                <div v-else class="empty editor-empty">填写商品资料和剪辑要求<br/>分析完成后，入选片段会显示在这里</div>
              </div>
            </div>
          </section>
          </template>
        </WorkspaceSplit>
      </template>
<section v-else class="panel empty">项目暂未加载，请刷新状态或返回视频项目。<button class="secondary" @click="workspace.goProjects()">返回视频项目</button></section>

<dialog ref="coverDialog" class="modal" aria-labelledby="cover-dialog-title" @cancel.prevent="closeCoverDraftDialog">
  <div class="modal-box cover-dialog-box">
    <h3 id="cover-dialog-title">生成带封面剪映草稿</h3>
    <form class="cover-image-search" @submit.prevent="loadCoverImages(true)">
      <label for="cover-image-keyword" class="sr-only">用于获取图片的商品标题</label>
      <input id="cover-image-keyword" v-model="coverImageKeyword" class="input input-sm w-full"
        type="text" maxlength="200" placeholder="输入商品标题" :disabled="loadingCoverImages" />
      <button type="submit" class="btn btn-sm" :disabled="!coverImageKeyword.trim()||loadingCoverImages||coverBusy">
        <span v-if="loadingCoverImages" class="loading loading-spinner loading-xs" aria-hidden="true"></span>获取图片
      </button>
      <button type="button" class="btn btn-sm" :disabled="uploadingCoverImage||loadingUploadedImages||coverBusy||Boolean(deletingCoverImage)"
        @click="coverUploadInput?.click()">
        <span v-if="uploadingCoverImage" class="loading loading-spinner loading-xs" aria-hidden="true"></span>上传图片
      </button>
      <input ref="coverUploadInput" type="file" hidden
        accept="image/png,image/jpeg,image/webp,.png,.jpg,.jpeg,.webp" @change="uploadCoverImage" />
    </form>
    <p v-if="matchedProductTitle" class="hint">匹配商品：{{ matchedProductTitle }}</p>
    <h4 class="cover-dialog-image-heading">商品参考图</h4>
    <div v-if="!coverReferences.length&&(loadingCoverImages||loadingUploadedImages)" class="cover-dialog-images" aria-label="商品参考图加载中" aria-busy="true">
      <div class="cover-reference-skeleton"><div class="skeleton"></div></div>
    </div>
    <div v-else-if="coverReferences.length" class="cover-dialog-images" role="group" aria-label="选择商品参考图">
      <div v-for="image in coverReferences" :key="image.id" class="cover-reference-item">
        <button type="button" class="cover-reference" :class="{'is-selected':selectedReferenceId===image.id}" :aria-pressed="selectedReferenceId===image.id" :disabled="productImageStates[image.id]==='error'" @click="selectedReferenceId=image.id">
          <div v-if="productImageStates[image.id]!=='loaded'" class="cover-reference-skeleton" :class="{'has-error':productImageStates[image.id]==='error'}" aria-hidden="true">
            <span v-if="productImageStates[image.id]==='error'">图片暂不可用</span>
            <div v-else class="skeleton"></div>
          </div>
          <img :src="image.url" :alt="image.uploaded?'本地参考图':'平台商品参考图'" :loading="selectedReferenceId===image.id?'eager':'lazy'" :class="{'is-loaded':productImageStates[image.id]==='loaded'}" @load="markProductImageLoaded(image.id)" @error="markProductImageFailed(image.id)" />
          <span v-if="selectedReferenceId===image.id">已选择</span>
        </button>
        <button v-if="image.uploaded" type="button" class="btn btn-circle btn-xs cover-reference-delete"
          aria-label="删除上传的参考图" title="删除上传的参考图"
          :disabled="Boolean(deletingCoverImage)||coverBusy||uploadingCoverImage" @click="deleteCoverImage(image.id)">×</button>
      </div>
    </div>
    <p v-if="platformImageError" class="hint">
      {{ platformImageError }}
      <button v-if="platformLoginRequired" type="button" class="btn btn-link btn-xs"
        @click="router.push({path:'/settings',query:{cloudTab:'product-platform',platform:platformLoginRequired}})">打开平台设置</button>
    </p>
    <p v-else-if="!coverReferences.length&&!loadingCoverImages&&!loadingUploadedImages" class="hint">获取商品图片，或上传 PNG、JPEG、WebP 图片（最大 20 MB）。</p>
    <div class="cover-dialog-workarea">
      <CoverPromptPicker v-if="showCoverPrompts" v-model="coverPrompt" :disabled="Boolean(activeCoverJob)||coverBusy" />
      <section class="cover-dialog-preview" aria-label="生成的封面预览">
        <div class="cover-preview-heading">
          <span class="cover-dialog-preview-label">生成封面</span>
          <button v-if="selectedCoverJob" type="button" class="btn btn-ghost btn-xs" @click="openOriginal">查看原图</button>
        </div>
        <div class="cover-preview-frame" :aria-busy="Boolean(activeCoverJob)||Boolean(selectedCoverJob&&coverPreviewState==='loading')">
          <template v-if="selectedCoverJob">
            <div v-if="coverPreviewState==='loading'" class="cover-preview-skeleton"><div class="skeleton"></div></div>
            <div v-else-if="coverPreviewState==='error'" class="cover-preview-empty">封面预览加载失败</div>
            <img :src="coverMediaUrl(selectedCoverJob.id)" alt="生成的 3:4 剪映草稿封面预览" :class="{'is-loaded':coverPreviewState==='loaded'}" @load="markCoverPreviewLoaded" @error="markCoverPreviewFailed" />
          </template>
          <div v-else-if="activeCoverJob" class="cover-preview-skeleton" aria-live="polite"><div class="skeleton"></div><span>封面生成中…</span></div>
          <div v-else class="cover-preview-empty">生成后在此预览</div>
        </div>
      </section>
    </div>
    <p v-if="coverError" class="error-text" role="alert">{{ coverError }}</p>
    <div class="modal-action">
      <button type="button" class="btn" :disabled="coverBusy" @click="closeCoverDraftDialog">关闭</button>
      <button type="button" class="btn btn-primary" :disabled="!canGenerateCover" @click="generateCover">{{ activeCoverJob?'封面生成中…':coverBusy?'正在提交…':'生成封面' }}</button>
      <button v-if="selectedCoverJob" type="button" class="btn btn-success" :disabled="busy||planDirty" @click="createCoveredDraft">创建带封面草稿</button>
    </div>
  </div>
  <form method="dialog" class="modal-backdrop" @submit.prevent="closeCoverDraftDialog"><button>关闭</button></form>
</dialog>
<dialog ref="originalDialog" class="modal" aria-labelledby="original-dialog-title" @cancel.prevent="closeOriginal">
  <div class="modal-box cover-original-box">
    <div class="cover-original-heading"><h3 id="original-dialog-title">封面原图</h3><button type="button" class="btn btn-sm" @click="closeOriginal">关闭</button></div>
    <div class="cover-original-content" :aria-busy="originalState==='loading'">
      <p v-if="originalState==='loading'" class="hint">原图加载中…</p>
      <p v-else-if="originalState==='error'" class="error-text" role="alert">原图不可用，请重新生成封面。</p>
      <img v-if="originalJobId" v-show="originalState==='loaded'" :src="originalMediaUrl(originalJobId)" alt="生图服务返回的完整原图" @load="originalState='loaded'" @error="originalState='error'" />
    </div>
  </div>
  <form method="dialog" class="modal-backdrop" @submit.prevent="closeOriginal"><button>关闭</button></form>
</dialog>
</template>
