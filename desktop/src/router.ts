import { createRouter, createWebHashHistory, type RouterHistory } from 'vue-router'
import type { Pinia } from 'pinia'
import { useSessionStore } from './stores/session'
import { useProjectStore } from './stores/project'
import { filterQuery, projectFilter } from './stores/projects'
import ProjectsView from './views/ProjectsView.vue'
import WorkspaceView from './views/WorkspaceView.vue'
import JobsView from './views/JobsView.vue'
import SettingsView from './views/SettingsView.vue'
import AppearanceView from './views/AppearanceView.vue'
import LocalSettingsView from './views/LocalSettingsView.vue'
import LiveMonitoringView from './views/LiveMonitoringView.vue'

export function createAppRouter(pinia: Pinia, history: RouterHistory = createWebHashHistory()) {
  const session=useSessionStore(pinia),project=useProjectStore(pinia)
  const router=createRouter({history,routes:[
    {path:'/',redirect:'/projects'},
    {path:'/projects',name:'projects',component:ProjectsView},
    {path:'/projects/:id/edit',name:'workspace',component:WorkspaceView},
    {path:'/live-monitoring',name:'live-monitoring',component:LiveMonitoringView},
    {path:'/jobs',name:'jobs',component:JobsView},
    {path:'/appearance',name:'appearance',component:AppearanceView},
    {path:'/settings',name:'settings',component:SettingsView},
    {path:'/local-settings',name:'local-settings',component:LocalSettingsView},
    {path:'/:pathMatch(.*)*',redirect:'/projects'},
  ],scrollBehavior:(_to,_from,saved)=>saved || {left:0,top:0}})
  let navigation=0
  router.beforeEach(async(to,from)=>{
    const current=++navigation
    session.navigating=true;project.invalidate()
    try {
      if(session.busy && from.matched.length) throw new Error('当前操作正在保存或提交，请完成后再切换页面')
      session.error=''
      if(to.name==='projects') {
        const query=filterQuery(projectFilter(to.query))
        if(JSON.stringify(query)!==JSON.stringify(to.query)) return {name:'projects',query,replace:true}
      }
      await session.connect()
      if(current!==navigation) return false
      if(to.name==='workspace') project.checkSwitch(String(to.params.id))
      await project.persistDraft()
      if(current!==navigation) return false
      if(to.name==='workspace') await project.load(String(to.params.id),()=>current===navigation)
      return current===navigation
    } catch(cause) {
      if(current===navigation) session.report(cause)
      // On a failed direct entry keep the requested address and render a retryable empty view.
      return !from.matched.length && current===navigation
    } finally {if(current===navigation) session.navigating=false}
  })
  router.onError(session.report)
  return router
}
