// Exercise real Router guards/history and Pinia stores against an isolated API fixture.
const assert=require('node:assert/strict')
const path=require('node:path')
const {createPinia,setActivePinia}=require('pinia')
const {createMemoryHistory,isNavigationFailure,NavigationFailureType}=require('vue-router')
const {modules}=require('./test-modules.cjs')
const copy=value=>JSON.parse(JSON.stringify(value))
const draft={product_name:'奶黄千层',product_info:'柔软面料',instructions:'去掉价格',target_duration_us:60e6}
const clip={candidate_id:'candidate',source_asset_id:'a',source_start_us:0,source_end_us:3e6,timeline_start_us:0,audio_gain_db:0}
const plan={id:'plan-a',product_id:'product-a',revision:1,status:'needs_review',schema_version:'1.0',
  canvas:{width:160,height:288,fps:25},target_duration_us:60e6,instructions:'去掉价格',warnings:[],subtitles:[],clips:[clip]}
let requests=[],saveFailure=false,releaseB,holdB=false
const drafts={a:copy(draft),b:{...draft,product_name:'其他商品'}}
const api=async(url,method='GET',body)=>{
  requests.push({url,method,body})
  if(url==='/auth') return {}
  if(url==='/jobs-summary') return {active:0,total:0,updated_at:0}
  if(url.startsWith('/projects?')) {
    const query=new URLSearchParams(url.split('?')[1]),page=Number(query.get('page'))
    return {items:[],total:43,page:Math.min(page,3),page_size:20,pages:3}
  }
  if(url==='/jobs') return []
  if(url.startsWith('/projects/')) {
    const id=url.split('/').at(-1)
    if(!drafts[id]) throw new Error('找不到对应记录')
    if(method==='PATCH') {
      if(saveFailure) throw new Error('资料保存失败')
      drafts[id]=copy(body);return copy(body)
    }
    if(id==='b' && holdB) await new Promise(resolve=>{releaseB=resolve})
    return copy({asset:{id,name:`${id}.mp4`,path:'mock.mp4',width:160,height:288,duration_us:180e6},
      draft:drafts[id],plans:id==='a'?[plan]:[],candidates:[],artifacts:[],jobs:[],latest_analysis:null})
  }
  throw new Error(`Unexpected request ${method} ${url}`)
}
function environment() {
  const pinia=createPinia();setActivePinia(pinia)
  const loaded=modules({[path.resolve(__dirname,'../src/api.ts')]:{api},
    [path.resolve(__dirname,'../src/events.ts')]:{subscribeJobEvents:()=>()=>{}}})
  const router=loaded.load('router.ts').createAppRouter(pinia,createMemoryHistory())
  return {pinia,router,loaded,session:loaded.load('stores/session.ts').useSessionStore(pinia),
    project:loaded.load('stores/project.ts').useProjectStore(pinia),
    list:loaded.load('stores/projects.ts').useProjectsStore(pinia),
    workspace:loaded.load('workspace.ts').createWorkspace(router)}
}
async function travel(router,delta) {
  await new Promise((resolve,reject)=>{
    const timer=setTimeout(()=>{remove();reject(Error('History navigation did not finish'))},2000)
    const remove=router.afterEach(()=>{clearTimeout(timer);remove();resolve()})
    router.go(delta)
  })
}
async function verify() {
  const env=environment(),{router,project,list,session,workspace,loaded}=env
  await router.push('/projects?page=2&search=奶黄&status=completed');await router.isReady()
  assert.equal(session.connected,true)
  await workspace.refresh()
  assert.deepEqual({...list.filter},{page:2,search:'奶黄',status:'completed'})
  const listUrl=router.currentRoute.value.fullPath
  await workspace.openProject('a')
  assert.equal(router.currentRoute.value.fullPath,'/projects/a/edit')
  assert.equal(project.assetId,'a');assert.equal(project.selectedPlan.id,'plan-a')
  project.productInfo='离开自动保存'
  project.selectedPlan.clips[0].source_end_us=2e6
  assert.equal(project.planDirty,true)
  await workspace.go('jobs')
  assert.equal(drafts.a.product_info,'离开自动保存')
  await travel(router,-1)
  assert.equal(router.currentRoute.value.fullPath,'/projects/a/edit')
  assert.equal(project.planDirty,true,'Returning with browser Back preserves the session draft')
  await travel(router,1)
  assert.equal(router.currentRoute.value.name,'jobs')
  const rejected=await router.push('/projects/b/edit')
  assert.ok(isNavigationFailure(rejected,NavigationFailureType.aborted))
  assert.equal(router.currentRoute.value.name,'jobs')
  assert.equal(project.assetId,'a');assert.ok(session.error.includes('未保存调整'))
  await workspace.goProjects()
  assert.equal(decodeURI(router.currentRoute.value.fullPath),decodeURI(listUrl))
  await workspace.openProject('a')
  assert.equal(project.selectedPlan.clips[0].source_end_us,2e6)
  project.selectedPlan=copy(project.plans[0])
  project.productName='保存失败时保留输入'
  saveFailure=true
  await workspace.go('settings')
  assert.equal(router.currentRoute.value.name,'workspace')
  assert.equal(project.productName,'保存失败时保留输入')
  assert.equal(session.error,'资料保存失败')
  saveFailure=false
  session.busy=true
  await workspace.go('jobs')
  assert.equal(router.currentRoute.value.name,'workspace','An in-flight mutation cannot navigate into another project')
  session.busy=false
  await workspace.go('settings')
  assert.equal(router.currentRoute.value.name,'settings')
  // Navigation cancellation must also prevent the old detail request from replacing Pinia state.
  holdB=true
  const pending=router.push('/projects/b/edit')
  for(let n=0;n<30 && !releaseB;n++) await Promise.resolve()
  assert.equal(typeof releaseB,'function')
  await workspace.refresh()
  await router.push('/jobs')
  releaseB();await pending;holdB=false
  assert.equal(router.currentRoute.value.name,'jobs')
  assert.equal(project.assetId,'a','Cancelled project loads cannot commit stale state')
  await router.push('/projects/b/edit')
  assert.equal(project.assetId,'b');assert.equal(project.selectedPlan,null)
  await router.push('/projects/missing/edit')
  assert.equal(router.currentRoute.value.fullPath,'/projects/b/edit')
  assert.equal(project.assetId,'b')
  // A new Pinia/router pair simulates refreshing a bookmarked project URL.
  const reopened=environment()
  await reopened.router.push('/projects/a/edit')
  assert.equal(reopened.project.productName,'保存失败时保留输入')
  assert.equal(reopened.project.productInfo,'离开自动保存')
  assert.equal(reopened.project.selectedPlan.clips[0].source_end_us,3e6,'Reload restores only the saved cut')
  assert.ok(!requests.some(r=>r.url.endsWith('/analyze')),'Deep links/history must not trigger paid analysis')
  const invalid=loaded.load('stores/projects.ts').projectFilter({page:'-2',search:['ignored'],status:'toString'})
  assert.deepEqual(invalid,{page:1,search:'',status:'all'})
  await reopened.router.push('/projects?page=-2&status=toString')
  assert.equal(reopened.router.currentRoute.value.fullPath,'/projects')
  await reopened.router.push('/projects?page=99&search=针织')
  await reopened.workspace.refresh()
  assert.equal(reopened.router.currentRoute.value.query.page,'3','Server page clamping updates the URL')
  await reopened.router.push('/unknown-url')
  assert.equal(reopened.router.currentRoute.value.name,'projects')
  assert.ok(!requests.some(r=>['/assets','/plans','/candidates','/exports'].includes(r.url)))
  console.log('Router/Pinia checks passed: deep links, query/history restore, draft/save guards, cancellation, isolation and no cloud submission.')
}
verify().catch(error=>{console.error(error);process.exitCode=1})
