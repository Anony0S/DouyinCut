// Exercise the actual App workflow without network, credentials or a real video.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vue = require('vue')
const { parse, compileScript } = require('vue/compiler-sfc')
const ts = require('typescript')

const {modules}=require('./test-modules.cjs')
const {createPinia,setActivePinia,storeToRefs}=require('pinia')
const {createMemoryHistory}=require('vue-router')
const copy = value => JSON.parse(JSON.stringify(value))
let bindings, uploaded, analyzeBody, exported, savedDraft, approvalCount = 0
let releaseOtherProject
const requests=[]
const asset = { id: 'video', name: '奶黄千层.mp4', path: 'local.mp4', duration_us: 250e6, width: 160, height: 120 }
const lists = { '/assets': [], '/products': [], '/candidates': [], '/plans': [], '/jobs': [], '/exports': [] }
const config = { region: 'cn', base_url: 'https://service.example/v1', planner_model: 'text', asr_model: 'audio',
  oss_endpoint: 'https://oss.example', oss_bucket: 'bucket',
  chat_timeout_seconds: 300 }
const api = async (url, method = 'GET', body) => {
  requests.push(url)
  if (url === '/auth') return {}
  if (url === '/jobs-summary') return {active:0}
  if (url.startsWith('/projects?')) return {items:lists['/assets'].map(item=>({...item,product_name:savedDraft?.product_name||'',status:'pending',updated_at:1,plan_id:null,output_duration_us:null})),total:lists['/assets'].length,page:1,page_size:20,pages:1}
  if (url.startsWith('/jobs/') && !url.endsWith('/retry') && !url.endsWith('/transcript')) return copy(lists['/jobs'].find(task=>url===`/jobs/${task.id}`))
  if (url === '/projects/video') {
    if(method==='PATCH') { savedDraft=copy(body); return copy(body) }
    return copy({asset,draft:savedDraft||{product_name:'',product_info:'',instructions:bindings.defaultInstructions,target_duration_us:60e6},
      plans:lists['/plans'],candidates:lists['/candidates'],artifacts:lists['/exports'],jobs:lists['/jobs'],latest_analysis:lists['/jobs'].find(task=>task.kind==='analyze')||null})
  }
  if (url === '/projects/other') {
    if(method==='GET') {
      await new Promise(resolve=>{releaseOtherProject=resolve})
      return copy({asset:{...asset,id:'other',name:'其他商品.mp4'},
        draft:{product_name:'另一款商品',product_info:'另一份资料',instructions:'保留面料介绍',target_duration_us:90e6},
        plans:[],candidates:[],artifacts:[],jobs:[],latest_analysis:null})
    }
    return copy(body)
  }
  if (url === '/settings/cloud') return { config, secrets: {}, secret_previews: {} }
  if (url === '/jobs/analysis/transcript') return { ready: true, sentences: [
    {start_us: 0, end_us: 5e6, text: '上身显瘦。'}, {start_us: 20e6, end_us: 25e6, text: '面料柔软。'}] }
  if (url === '/jobs/analysis/retry') {
    const task = lists['/jobs'].find(job => job.id === 'analysis')
    task.state = 'queued'; task.error = null; task.payload.config = copy(config)
    return copy(task)
  }
  if (lists[url]) return copy(lists[url])
  if (url === '/assets/video/analyze') {
    analyzeBody = copy(body)
    const job = { id: 'analysis', kind: 'analyze', state: 'queued', progress: 0, payload: { asset_id: 'video', ...body } }
    lists['/jobs'].unshift(job)
    return copy(job)
  }
  if (url === '/plans/roughcut' && method === 'PATCH') {
    const saved = { ...copy(body), revision: body.revision + 1, status: 'needs_review' }
    lists['/plans'] = [saved]
    return copy(saved)
  }
  if (url === '/plans/roughcut/approve') {
    approvalCount++
    const saved = { ...lists['/plans'][0], revision: lists['/plans'][0].revision + 1, status: 'approved' }
    lists['/plans'] = [saved]
    return copy(saved)
  }
  if (url === '/plans/roughcut/exports') { exported = copy(body); return {} }
  throw new Error(`Unexpected route ${url}`)
}
const uploadVideo = async file => {
  uploaded = file
  const job = { id: 'import', kind: 'import', state: 'queued', progress: 0, payload: {} }
  lists['/jobs'].unshift(job)
  return copy(job)
}
const loaded=modules({[path.resolve(__dirname,'../src/api.ts')]:{api,uploadVideo},
  [path.resolve(__dirname,'../src/events.ts')]:{subscribeJobEvents:()=>()=>{}}})
const pinia=createPinia();setActivePinia(pinia)
const {createAppRouter}=loaded.load('router.ts')
const router=createAppRouter(pinia,createMemoryHistory())
const project=loaded.load('stores/project.ts').useProjectStore(pinia)
const session=loaded.load('stores/session.ts').useSessionStore(pinia)
const workspace=loaded.load('workspace.ts').createWorkspace(router)
const {provideVideoImport}=loaded.load('composables/useVideoImport.ts')
const node = type => ({ type, children: [], parent: null })
const renderer = vue.createRenderer({
  createElement: node, createText: node, createComment: node,
  setText() {}, setElementText() {}, patchProp() {},
  insert(el, parent) { parent.children.push(el); el.parent = parent }, remove() {},
  parentNode: el => el.parent, nextSibling: () => null,
})
const settle = async () => { for (let i = 0; i < 12; i++) { await Promise.resolve(); await vue.nextTick() } }

async function verify() {
  const app = renderer.createApp({ setup() {
    bindings={...storeToRefs(project),...storeToRefs(session),...project,
      ...storeToRefs(project),...storeToRefs(session),...workspace,...provideVideoImport(workspace),
      defaultInstructions:project.defaultInstructions,subtitlePath:loaded.load('presentation.ts').subtitlePath,
      page:vue.computed(()=>router.currentRoute.value.name),
      navigate:name=>name==='projects' ? workspace.goProjects() : workspace.go(name)}
    return () => vue.h('div')
  } })
  app.use(pinia);app.mount(node('root'));await router.push('/projects');await router.isReady();await workspace.refresh(); await settle()
  try {
    assert.equal(bindings.error.value, '')
    assert.equal(bindings.page.value,'projects')
    const file = { name: '奶黄千层.mp4' }
    const input = { files: [file], value: 'selected-file' }
    await bindings.selectFile({ target: input })
    assert.equal(uploaded, file)
    assert.equal(input.value, '')
    assert.equal(bindings.pendingImportId.value, 'import')
    lists['/assets'] = [asset]
    Object.assign(lists['/jobs'][0], { state: 'succeeded', result: { asset_id: 'video' } })
    await bindings.refresh()
    assert.equal(bindings.assetId.value, 'video')
    assert.equal(bindings.page.value, 'workspace')
    assert.equal(bindings.assets.value.length, 1)
    await bindings.analyze()
    assert.equal(bindings.error.value, '请先填写商品名称')
    assert.equal(analyzeBody, undefined)
    bindings.productName.value = ' 奶黄千层 '
    bindings.productInfo.value = '假两件针织开衫'
    bindings.instructions.value = ''
    bindings.refreshAsr.value = true
    await bindings.analyze()
    assert.equal(analyzeBody.product_name, '奶黄千层')
    assert.equal(analyzeBody.product_info, '假两件针织开衫')
    assert.equal(analyzeBody.target_duration_us, 60e6)
    assert.equal(analyzeBody.refresh_asr, true)
    assert.equal(bindings.refreshAsr.value, false, 'Explicit ASR refresh must not remain enabled for future tasks')
    assert.ok(analyzeBody.instructions.includes('去掉价格'))
    assert.equal(bindings.analyzingAsset.value, true)
    const clip = (id, start, timeline) => ({ candidate_id: id, source_asset_id: 'video', source_start_us: start,
      source_end_us: start + 5e6, timeline_start_us: timeline, audio_gain_db: 0 })
    const plan = { id: 'roughcut', product_id: 'product', revision: 1, status: 'needs_review', schema_version: '1.0',
      target_duration_us: 60e6, canvas: { width: 160, height: 120, fps: 30 },
      clips: [clip('fabric', 20e6, 0), clip('fit', 0, 5e6)], subtitles: [], warnings: [], instructions: 'default' }
    lists['/plans'] = [plan]
    Object.assign(lists['/jobs'][0], { state: 'succeeded', result: { asset_id: 'video', plan_id: 'roughcut' } })
    lists['/exports'] = [{ id: 'preview1', plan_id: 'roughcut', revision: 1, format: 'preview' }]
    await bindings.refresh()
    assert.equal(bindings.selectedPlan.value.id, 'roughcut')
    assert.equal(bindings.previewArtifact.value.id, 'preview1')
    assert.equal(bindings.analyzingAsset.value, false)
    await bindings.loadTranscript()
    assert.equal(bindings.transcriptVisible.value, true)
    assert.equal(bindings.transcript.value.sentences.length, 2)
    bindings.moveClip(1, -1)
    assert.equal(bindings.planDirty.value, true)
    assert.equal(bindings.plans.value[0].clips[0].candidate_id, 'fabric', 'Draft must be isolated from saved state')
    await bindings.approveAndExport(bindings.selectedPlan.value)
    assert.equal(approvalCount, 0, 'Unsaved edits cannot export')
    await bindings.savePlan()
    assert.equal(bindings.selectedPlan.value.revision, 2)
    assert.equal(bindings.selectedPlan.value.clips[1].timeline_start_us, 5e6)
    assert.equal(bindings.planDirty.value, false)
    assert.equal(bindings.previewArtifact.value, undefined, 'Previous revision cannot represent changed cuts')
    lists['/exports'].unshift({ id: 'preview2', plan_id: 'roughcut', revision: 2, format: 'preview' })
    await bindings.refresh()
    await bindings.approveAndExport(bindings.selectedPlan.value)
    assert.deepEqual(exported, { format: 'mp4' })
    assert.equal(approvalCount, 1)
    assert.equal(bindings.selectedPlan.value.revision, 3)
    lists['/exports'].unshift({ id: 'mp4', plan_id: 'roughcut', revision: 3, format: 'mp4',
      path:'C:\\成片\\当前版本',video_path:'C:\\成片\\当前版本\\video.mp4',duration_us:10e6 })
    await bindings.refresh()
    assert.equal(bindings.previewArtifact.value.id, 'mp4')
    assert.deepEqual(bindings.projectExports.value.map(item=>item.id),['mp4'],'Only final exports are shown as project deliveries')
    assert.equal(bindings.subtitlePath(bindings.projectExports.value[0]),'C:\\成片\\当前版本\\subtitles.srt')
    lists['/exports'].push({id:'history',plan_id:'roughcut',revision:1,format:'mp4',
      path:'/generated/history/',video_path:'/generated/history/video.mp4',duration_us:10e6})
    lists['/exports'].push({id:'foreign',plan_id:'another-project-plan',revision:1,format:'mp4',
      path:'/generated/other',video_path:'/generated/other/video.mp4',duration_us:10e6})
    await bindings.refresh()
    assert.deepEqual(bindings.projectExports.value.map(item=>item.id),['mp4','history'],'Saved revision exports remain accessible without mixing another project')
    assert.equal(bindings.subtitlePath(bindings.projectExports.value[1]),'/generated/history/subtitles.srt')
    await bindings.approveAndExport(bindings.selectedPlan.value)
    assert.equal(approvalCount, 1, 'Repeated export need not approve another revision')
    // Selecting a saved plan uses a clone even when objects are already Vue proxies.
    await bindings.openProject('video')
    assert.equal(bindings.selectedPlan.value.id, 'roughcut')
    const failed = lists['/jobs'].find(job => job.id === 'analysis')
    failed.state = 'failed'; failed.error = '模型调用失败'
    config.asr_model = 'qwen-audio-3.1-asr-flash'
    await bindings.controlJob(copy(failed), 'retry')
    assert.equal(bindings.pendingAnalyzeId.value, 'analysis')
    assert.equal(bindings.transcript.value, null)
    assert.ok(bindings.message.value.includes('qwen-audio-3.1-asr-flash'), 'Retry reports the saved speech model actually used')
    bindings.productInfo.value='已保存的补充资料'
    await bindings.navigate('projects')
    assert.equal(savedDraft.product_info,'已保存的补充资料')
    assert.equal(bindings.page.value,'projects')
    await bindings.openProject('video')
    assert.equal(bindings.productInfo.value,'已保存的补充资料')
    assert.equal(bindings.selectedPlan.value.id,'roughcut')
    bindings.moveClip(1,-1)
    await bindings.openProject('other')
    assert.ok(bindings.error.value.includes('未保存调整'))
    assert.equal(bindings.assetId.value,'video')
    await bindings.navigate('projects')
    await bindings.openProject('video')
    assert.equal(bindings.planDirty.value,true,'Returning to the same project preserves unsaved clip edits')
    bindings.selectedPlan.value=copy(bindings.plans.value[0])
    const opening=bindings.openProject('other')
    await settle()
    assert.equal(typeof releaseOtherProject,'function')
    await bindings.refresh()
    releaseOtherProject();await opening
    assert.equal(bindings.assetId.value,'other','Background refresh cannot invalidate explicit project opening')
    assert.equal(bindings.productName.value,'另一款商品')
    assert.equal(bindings.targetSeconds.value,90)
    assert.equal(bindings.selectedPlan.value,null,'New projects do not display another project’s plan')
    assert.equal(bindings.projectExports.value.length,0,'Switching projects clears previous deliveries')
    assert.equal(bindings.transcriptVisible.value,false)
    await bindings.openProject('video')
    assert.equal(bindings.productInfo.value,'已保存的补充资料')
    assert.equal(bindings.selectedPlan.value.id,'roughcut')
    assert.ok(!requests.includes('/assets') && !requests.includes('/plans') && !requests.includes('/candidates'), 'Editor must not fetch the full library')
    assert.ok(!requests.includes('/exports'),'Project deliveries must not fetch a global export list')
    console.log('ASR roughcut workflow checks passed (local file, product input, automatic preview, edit and export).')
  } finally { app.unmount() }
}
verify().catch(error => { console.error(error); process.exitCode = 1 })
