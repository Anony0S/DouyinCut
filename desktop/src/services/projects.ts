import { api, uploadVideo, type ProjectDetail, type ProjectDraft, type ProjectPage, type Plan, type Job } from '../api'

export type ProjectFilter = { page: number; search: string; status: string }
export const projectsApi = {
  saveEditingDefaults: (instructions: string) => api<{instructions:string}>('/settings/editing-defaults', 'PUT', {instructions}),
  list: (filter: ProjectFilter) => api<ProjectPage>(`/projects?${new URLSearchParams({page:String(filter.page),page_size:'20',search:filter.search,status:filter.status})}`),
  detail: (id: string) => api<ProjectDetail>(`/projects/${encodeURIComponent(id)}`),
  saveDraft: (id: string, draft: ProjectDraft) => api<ProjectDraft>(`/projects/${encodeURIComponent(id)}`, 'PATCH', draft),
  deleteProject: (id: string) => api<{deleted_asset_id:string;deleted_plans:number;oss_audio_retained:boolean;cleanup_pending:string[]}>(`/projects/${encodeURIComponent(id)}`, 'DELETE'),
  upload: uploadVideo,
  analyze: (id: string, draft: ProjectDraft, refreshAsr: boolean) => api<Job>(`/assets/${encodeURIComponent(id)}/analyze`, 'POST', {...draft,refresh_asr:refreshAsr}),
  savePlan: (plan: Plan) => api<Plan>(`/plans/${encodeURIComponent(plan.id)}`, 'PATCH', plan),
  deletePlan: (id: string) => api<{deleted_plan_id:string;source_asset_id:string;cleanup_pending:string[]}>(`/plans/${encodeURIComponent(id)}`, 'DELETE'),
  createJianyingDraft: (id: string, revision: number) => api<Job>(`/plans/${encodeURIComponent(id)}/jianying-drafts`, 'POST', {revision}),
  transcript: (id: string) => api<TranscriptResult>(`/jobs/${encodeURIComponent(id)}/transcript`),
}
export type TranscriptResult = { ready: boolean; sentences: {start_us:number;end_us:number;text:string}[] }
