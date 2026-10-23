import { api, type Job } from '../api'

export const jobsApi = {
  list: () => api<Job[]>('/jobs'),
  summary: () => api<{active:number;total:number;updated_at:number}>('/jobs-summary'),
  get: (id: string) => api<Job>(`/jobs/${encodeURIComponent(id)}`),
  control: (id: string, action: string) => api<Job>(`/jobs/${encodeURIComponent(id)}/${action}`, 'POST'),
}
