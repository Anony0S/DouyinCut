import { api, apiJson, uploadVideo, type ProjectDetail, type ProjectDraft, type ProjectPage, type Plan, type Job } from '../api'

export type ProjectFilter = { page: number; search: string; status: string }
export type ShortFillerFilter = { max_chars: number; max_duration_ms: number }
export type CoverReference = { id: string; url: string }
export const projectsApi = {
  saveEditingDefaults: (instructions: string) => api<{instructions:string}>('/settings/editing-defaults', 'PUT', {instructions}),
  list: (filter: ProjectFilter) => api<ProjectPage>(`/projects?${new URLSearchParams({page:String(filter.page),page_size:'20',search:filter.search,status:filter.status})}`),
  detail: (id: string) => api<ProjectDetail>(`/projects/${encodeURIComponent(id)}`),
  saveDraft: (id: string, draft: ProjectDraft) => api<ProjectDraft>(`/projects/${encodeURIComponent(id)}`, 'PATCH', draft),
  deleteProject: (id: string) => api<{deleted_asset_id:string;deleted_plans:number;oss_audio_retained:boolean;cleanup_pending:string[]}>(`/projects/${encodeURIComponent(id)}`, 'DELETE'),
  upload: uploadVideo,
  importLocal: (path: string) => api<Job>('/assets/import-local', 'POST', { path }),
  analyze: (id: string, draft: ProjectDraft, options: { refresh_asr: boolean; short_filler_filter: ShortFillerFilter | null }) =>
    api<Job>(`/assets/${encodeURIComponent(id)}/analyze`, 'POST', { ...draft, ...options }),
  savePlan: (plan: Plan) => api<Plan>(`/plans/${encodeURIComponent(plan.id)}`, 'PATCH', plan),
  deletePlan: (id: string) => api<{deleted_plan_id:string;source_asset_id:string;cleanup_pending:string[]}>(`/plans/${encodeURIComponent(id)}`, 'DELETE'),
  createJianyingDraft: (id: string, revision: number, coverId?: string) => api<Job>(`/plans/${encodeURIComponent(id)}/jianying-drafts`, 'POST', {revision, ...(coverId ? {cover_id:coverId} : {})}),
  uploadCoverReference: (assetId: string, file: File) => apiJson<CoverReference>(
    `/assets/${encodeURIComponent(assetId)}/cover-references`,
    { method: 'POST', headers: { 'Content-Type': 'application/octet-stream' }, body: file },
  ),
  uploadedCoverReferences: (assetId: string) =>
    api<{images: CoverReference[]}>(`/assets/${encodeURIComponent(assetId)}/cover-references`),
  deleteCoverReference: (assetId: string, referenceId: string) =>
    api<{deleted: boolean}>(`/assets/${encodeURIComponent(assetId)}/cover-references/${encodeURIComponent(referenceId)}`, 'DELETE'),
  generateCover: (assetId:string, planId:string, revision:number, referenceId:string, prompt:string) =>
    api<Job>(`/assets/${encodeURIComponent(assetId)}/covers`, 'POST', {plan_id:planId, revision, reference_id:referenceId, prompt}),
  transcript: (id: string) => api<TranscriptResult>(`/jobs/${encodeURIComponent(id)}/transcript`),
}
export type TranscriptResult = { ready: boolean; sentences: {start_us:number;end_us:number;text:string}[] }
