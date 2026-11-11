import { inject, provide, type InjectionKey } from 'vue'
import type { Router } from 'vue-router'
import type { Job, CloudConfig } from './api'
import { subscribeJobEvents } from './events'
import { jobsApi } from './services/jobs'
import { useSessionStore } from './stores/session'
import { useProjectStore } from './stores/project'
import { useProjectsStore, filterQuery, projectFilter } from './stores/projects'
import type { ProjectFilter } from './services/projects'
import { useJobsStore } from './stores/jobs'

export function createWorkspace(router: Router) {
  const session=useSessionStore(),project=useProjectStore(),list=useProjectsStore(),jobs=useJobsStore()
  let stopEvents: (()=>void) | undefined, disposed=false, checkingImport=false
  async function push(destination: Parameters<Router['push']>[0]) {
    try {await router.push(destination)} catch(cause) {session.report(cause)}
  }
  async function goProjects(filter: ProjectFilter = list.filter) {await push({name:'projects',query:filterQuery(filter)})}
  async function openProject(id: string) {await push({name:'workspace',params:{id}})}
  async function goEditor() {if(project.assetId) await openProject(project.assetId)}
  async function go(name: 'jobs'|'settings'|'appearance'|'local-settings'|'logs'|'live-monitoring') {await push({name})}
  async function refreshProjects(filter: ProjectFilter) {
    try {
      await session.connect();await list.load(filter)
      const route=router.currentRoute.value
      if(route.name==='projects' && JSON.stringify(projectFilter(route.query))===JSON.stringify(filter) && list.projectPage.page!==filter.page) {
        await router.replace({name:'projects',query:filterQuery({...filter,page:list.projectPage.page})})
      }
    } catch(cause) {session.report(cause)}
  }
  async function checkImport() {
    if(!session.pendingImportId || checkingImport || session.navigating || session.busy) return
    checkingImport=true
    const id=session.pendingImportId
    try {
      const job=await jobsApi.get(id)
      if(job.state==='succeeded' && typeof job.result?.asset_id==='string') {
        await openProject(job.result.asset_id)
        if(router.currentRoute.value.name==='workspace' && project.assetId===job.result.asset_id) session.pendingImportId=''
      } else if(['failed','cancelled'].includes(job.state)) {session.error=job.error||'视频导入失败';session.pendingImportId=''}
    } finally {checkingImport=false}
  }
  async function refresh() {
    await session.connect()
    const route=router.currentRoute.value
    const tasks: Promise<unknown>[]=[session.refreshSummary()]
    if(!session.navigating) {
      if(route.name==='projects') tasks.push(refreshProjects(projectFilter(route.query)))
      if(route.name==='workspace') tasks.push(project.load(String(route.params.id),()=>!session.navigating && router.currentRoute.value.fullPath===route.fullPath))
      if(route.name==='jobs') tasks.push(jobs.load())
    }
    await Promise.all(tasks)
    await checkImport()
  }
  async function controlJob(job: Job, action: string) {
    await session.act(async()=>{
      const updated=await jobsApi.control(job.id,action)
      if(['retry','acknowledge_asr_uncertain'].includes(action) && job.kind==='analyze' && updated.payload?.asset_id===project.assetId) {
        project.trackRetry(updated)
        session.message=action==='acknowledge_asr_uncertain'
          ? `已解除第 ${job.id.slice(-6)} 个 ASR 未确定标记，使用语音模型 ${String((updated.payload?.config as CloudConfig).asr_model)} 重试`
          : `已使用保存的语音模型 ${String((updated.payload?.config as CloudConfig).asr_model)} 重试`
      }
      await refresh()
    })
  }
  async function start() {
    try {
      await router.isReady();await session.connect();await session.refreshSummary()
      if(disposed) return
      stopEvents=subscribeJobEvents({
        onJobs:()=>{void refresh().catch(session.report)},
        onOpen:()=>{if(session.error==='任务连接中断，正在尝试重连') session.error=''},
        onError:message=>{session.error=message},
      })
    } catch(cause) {session.report(cause)}
  }
  function stop() {disposed=true;stopEvents?.()}
  return {goProjects,openProject,goEditor,go,refreshProjects,refresh,controlJob,start,stop}
}
export type Workspace = ReturnType<typeof createWorkspace>
const key: InjectionKey<Workspace> = Symbol('workspace')
export function provideWorkspace(workspace: Workspace) {provide(key,workspace)}
export function useWorkspace() {
  const workspace=inject(key)
  if(!workspace) throw new Error('工作台服务未初始化')
  return workspace
}
