import { api, type Job } from '../api'

export const JOB_PAGE_SIZE = 20
export type JobCursor = Pick<Job, 'created' | 'id'>

export const jobsApi = {
  list: () => api<Job[]>('/jobs'),
  page: (before?: JobCursor) => {
    const query = new URLSearchParams({limit: String(JOB_PAGE_SIZE + 1)})
    if (before) {
      query.set('before_created', String(before.created))
      query.set('before_id', before.id)
    }
    return api<Job[]>(`/jobs?${query}`)
  },
  summary: () => api<{active:number;total:number;updated_at:number}>('/jobs-summary'),
  get: (id: string) => api<Job>(`/jobs/${encodeURIComponent(id)}`),
  control: (id: string, action: string) => api<Job>(`/jobs/${encodeURIComponent(id)}/${action}`, 'POST'),
}
