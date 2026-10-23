import { ref } from 'vue'
import { defineStore } from 'pinia'
import type { ProjectPage } from '../api'
import { projectsApi, type ProjectFilter } from '../services/projects'
import { projectStatusLabels } from '../presentation'
import type { LocationQuery } from 'vue-router'

export function projectFilter(query: LocationQuery): ProjectFilter {
  const rawPage=typeof query.page==='string' ? Number(query.page) : 1
  return {page:Number.isSafeInteger(rawPage) && rawPage>0 ? rawPage : 1,
    search:typeof query.search==='string' ? query.search.trim().slice(0,200) : '',
    status:typeof query.status==='string' && Object.hasOwn(projectStatusLabels,query.status) ? query.status : 'all'}
}
export function filterQuery(filter: ProjectFilter) {
  return {page:filter.page>1 ? String(filter.page) : undefined,search:filter.search || undefined,
    status:filter.status!=='all' ? filter.status : undefined}
}
export const useProjectsStore = defineStore('projects', () => {
  const projectPage=ref<ProjectPage>({items:[],total:0,page:1,page_size:20,pages:1})
  const filter=ref<ProjectFilter>({page:1,search:'',status:'all'}), loading=ref(false)
  let request=0
  async function load(next: ProjectFilter) {
    const current=++request;filter.value={...next};loading.value=true
    try {
      const result=await projectsApi.list(next)
      if(current===request) {projectPage.value=result;filter.value.page=result.page}
    } finally {if(current===request) loading.value=false}
  }
  async function remove(id: string) {
    const result=await projectsApi.deleteProject(id)
    await load({...filter.value})
    return result
  }
  return {projectPage,filter,loading,load,remove}
})
