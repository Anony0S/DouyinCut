import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import type { Asset, Candidate, Plan, Job, Artifact, ProjectDraft, ProjectDetail } from '../api'
import { projectsApi } from '../services/projects'
import { useSessionStore } from './session'

export const useProjectStore=defineStore('project',()=>{
const projectAnalysis = ref<Job | null>(null)
const savedDraft = ref('')
let detailRequest=0
const assets = ref<Asset[]>([]), candidates = ref<Candidate[]>([])
const plans = ref<Plan[]>([]), jobs = ref<Job[]>([]), artifacts = ref<Artifact[]>([])
const assetId = ref(''), productName = ref('')
const pendingAnalyzeId = ref('')
const defaultInstructions = '保留上身效果、版型、面料和穿着感受的介绍，去掉价格、优惠、催单、无关互动及重复口播，保持表达完整，生成约 60 秒的视频。'
const instructions = ref(defaultInstructions), targetSeconds = ref(60), toleranceSeconds = ref(15)
const refreshAsr = ref(false)
const transcript = ref<{ready:boolean;sentences:{start_us:number;end_us:number;text:string}[]} | null>(null)
const transcriptVisible = ref(false)
const session=useSessionStore()

const selectedPlan = ref<Plan | null>(null)
const activeAsset = computed(() => assets.value.find(a => a.id === assetId.value))
const assetPlans = computed(() => plans.value.filter(p => p.clips.every(c => c.source_asset_id === assetId.value)))
const previewArtifact = computed(() => selectedPlan.value ? artifacts.value.find(a => a.plan_id === selectedPlan.value!.id && ['preview','mp4'].includes(a.format) && a.revision === selectedPlan.value!.revision) : undefined)
const planDirty = computed(() => {
  const saved=plans.value.find(p=>p.id===selectedPlan.value?.id)
  return Boolean(saved && selectedPlan.value && JSON.stringify(saved.clips)!==JSON.stringify(selectedPlan.value.clips))
})
const assetJobs = computed(() => jobs.value.filter(j => j.payload?.asset_id === assetId.value || (j.payload?.plan_id && assetPlans.value.some(p => p.id === j.payload?.plan_id))).slice(0,5))
const analyzingAsset = computed(() => jobs.value.some(j => j.payload?.asset_id===assetId.value && j.kind === 'analyze' && ['queued','running','paused','pause_requested'].includes(j.state)))
const latestAnalysis = computed(() => projectAnalysis.value)
function currentDraft(): ProjectDraft {
  return {product_name:productName.value,instructions:instructions.value,
    target_duration_us:Math.round(targetSeconds.value*1e6),duration_tolerance_us:Math.round(toleranceSeconds.value*1e6)}
}
async function persistDraft() {
  if(!assetId.value || savedDraft.value===JSON.stringify(currentDraft())) return
  const id=assetId.value, draft=currentDraft()
  const saved=await projectsApi.saveDraft(id,draft)
  if(assetId.value===id) savedDraft.value=JSON.stringify(saved)
}
function clonePlan(plan?: Plan): Plan | null { return plan ? JSON.parse(JSON.stringify(plan)) : null }
function applyProject(detail: ProjectDetail, opening: boolean) {
  assets.value=[detail.asset];candidates.value=detail.candidates;plans.value=detail.plans
  jobs.value=detail.jobs;artifacts.value=detail.artifacts;projectAnalysis.value=detail.latest_analysis
  if(opening) {
    assetId.value=detail.asset.id
    productName.value=detail.draft.product_name
    instructions.value=detail.draft.instructions;targetSeconds.value=detail.draft.target_duration_us/1e6
    toleranceSeconds.value=detail.draft.duration_tolerance_us/1e6
    savedDraft.value=JSON.stringify(detail.draft)
    selectedPlan.value=clonePlan(detail.plans[0]);refreshAsr.value=false;transcript.value=null;transcriptVisible.value=false
  }
  if(pendingAnalyzeId.value) {
    const task=detail.jobs.find(job=>job.id===pendingAnalyzeId.value)
    if(task?.state==='succeeded' && typeof task.result?.plan_id==='string') {
      if(!planDirty.value) selectedPlan.value=clonePlan(detail.plans.find(plan=>plan.id===task.result!.plan_id))
      pendingAnalyzeId.value='';session.message='粗剪方案已生成，正在拼接预览'
    }
  }
  if(!selectedPlan.value && detail.plans.length) selectedPlan.value=clonePlan(detail.plans[0])
}

async function load(id: string, canApply: () => boolean = () => true) {
  const request=++detailRequest
  const detail=await projectsApi.detail(id)
  if(request===detailRequest && canApply()) applyProject(detail,assetId.value!==id)
}
async function refresh() {
  if(!assetId.value || session.navigating) return
  const id=assetId.value
  await load(id,()=>assetId.value===id && !session.navigating)
}
function invalidate() { detailRequest++ }
async function saveProjectInfo() {await session.act(async()=>{await persistDraft();session.message='项目资料与剪辑设置已保存'})}
async function saveDefaultInstructions() {await session.act(async()=>{
  const id=assetId.value
  const value=instructions.value.trim()||defaultInstructions
  await persistDraft()
  const saved=await projectsApi.saveEditingDefaults(value)
  if(assetId.value===id && (instructions.value.trim()||defaultInstructions)===value) instructions.value=saved.instructions
  session.message='剪辑要求已保存为默认；后续新项目自动使用，分析失败不会丢失'
})}
function checkSwitch(id?: string) {
  if((!id || id!==assetId.value) && planDirty.value) throw new Error('当前片段有未保存调整，请先保存，再打开其他项目或新建视频')
}
function trackRetry(updated: Job) {
  if(updated.kind==='analyze' && updated.payload?.asset_id===assetId.value) {
    pendingAnalyzeId.value=updated.id;transcript.value=null;transcriptVisible.value=false
  }
}
async function analyze() { await session.act(async()=>{
  if(!activeAsset.value) throw new Error('请先选择视频')
  if(!productName.value.trim()) throw new Error('请先填写商品名称')
  await persistDraft()
  const job=await projectsApi.analyze(assetId.value,{
    product_name:productName.value.trim(),
    instructions:instructions.value.trim()||defaultInstructions,target_duration_us:Math.round(targetSeconds.value*1e6),
    duration_tolerance_us:Math.round(toleranceSeconds.value*1e6),
  },refreshAsr.value)
  refreshAsr.value=false;transcript.value=null;transcriptVisible.value=false
  pendingAnalyzeId.value=job.id;session.message='已排队：完成全量转写 → 决策模型选择时间区间 → 裁剪拼接预览';await refresh()
}) }
async function loadTranscript() { await session.act(async()=>{
  const job=latestAnalysis.value
  if(!job) return
  const currentAsset=assetId.value
  const loaded=await projectsApi.transcript(job.id)
  if(assetId.value===currentAsset) { transcript.value=loaded;transcriptVisible.value=true }
}) }
async function createJianyingDraft(plan: Plan) { await session.act(async()=>{
  if(planDirty.value) throw new Error('请先保存片段调整，再生成剪映草稿')
  const task=await projectsApi.createJianyingDraft(plan.id,plan.revision)
  await refresh();session.message='剪映草稿生成任务已排队，完成后会显示草稿目录'
  return task
}) }
async function savePlan() { await session.act(async()=>{
  if(!selectedPlan.value) return
  const plan=JSON.parse(JSON.stringify(selectedPlan.value)) as Plan
  let cursor=0
  for(const clip of plan.clips){ clip.timeline_start_us=cursor; cursor+=clip.source_end_us-clip.source_start_us }
  const previous=plans.value.find(p=>p.id===plan.id)
  if(!previous||JSON.stringify(previous.clips)!==JSON.stringify(plan.clips)) plan.subtitles=[]
  // The API remaps source subtitles when clips change.
  selectedPlan.value=await projectsApi.savePlan(plan)
  await refresh(); session.message='修改已保存为新版本'
}) }
async function deletePlan(id: string) { await session.act(async()=>{
  const removed=plans.value.find(plan=>plan.id===id)
  if(!removed) throw new Error('粗剪方案不存在，请刷新后重试')
  const result=await projectsApi.deletePlan(id)
  const candidateIds=new Set(removed.clips.map(clip=>clip.candidate_id))
  plans.value=plans.value.filter(plan=>plan.id!==id)
  artifacts.value=artifacts.value.filter(artifact=>artifact.plan_id!==id)
  candidates.value=candidates.value.filter(candidate=>!candidateIds.has(candidate.id)||plans.value.some(plan=>plan.clips.some(clip=>clip.candidate_id===candidate.id)))
  if(selectedPlan.value?.id===id) selectedPlan.value=clonePlan(assetPlans.value[0])
  session.message=result.cleanup_pending.length?'方案记录已删除，部分本地文件暂时无法清理':'粗剪方案及其本地产物已删除；源视频、ASR 对话和 OSS 临时音频已保留'
}) }
/** Move a clip to an insertion slot: slot N means "before the clip currently at N", and the clip count means the end. */
function moveClipTo(from: number, slot: number) {
  const clips=selectedPlan.value?.clips
  if(!clips||from<0||from>=clips.length||slot<0||slot>clips.length||slot===from||slot===from+1) return
  const [clip]=clips.splice(from,1)
  clips.splice(slot>from?slot-1:slot,0,clip)
}
function moveClip(index: number, delta: number) { moveClipTo(index,delta<0?index+delta:index+delta+1) }

return {projectAnalysis,savedDraft,assets,candidates,plans,jobs,artifacts,assetId,productName,pendingAnalyzeId,
 defaultInstructions,instructions,targetSeconds,toleranceSeconds,refreshAsr,transcript,transcriptVisible,selectedPlan,
 activeAsset,assetPlans,previewArtifact,planDirty,assetJobs,analyzingAsset,latestAnalysis,
 currentDraft,persistDraft,saveProjectInfo,saveDefaultInstructions,load,refresh,invalidate,checkSwitch,trackRetry,
 analyze,loadTranscript,createJianyingDraft,savePlan,deletePlan,moveClip,moveClipTo}
})
