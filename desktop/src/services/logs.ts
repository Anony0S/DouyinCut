import { api } from '../api'

export type LogCategory = 'runtime' | 'task'
export type LogFile = { category: LogCategory; name: string; size: number; modified: number; group: string | null; group_type: 'job' | 'product_clip' | null; job_kind: string | null; product_name: string | null }
/** `next` is the byte offset to pass as `after` for the following read; `reset` means the file was cleared or replaced. */
export type LogChunk = { text: string; start: number; next: number; size: number; truncated: boolean; reset: boolean }
export type LogClearResult = { runtime_cleared: boolean; deleted_files: number; retained_files: number; bytes_reclaimed: number }

export const logsApi = {
  list: () => api<{ files: LogFile[]; runtime_available: boolean }>('/logs'),
  read: (file: Pick<LogFile, 'category' | 'name'>, after?: number) =>
    api<LogChunk>(`/logs/${file.category}/${encodeURIComponent(file.name)}${after === undefined ? '' : `?after=${after}`}`),
  open: (category: LogCategory, name?: string) => api<{ opened: boolean }>('/logs/open', 'POST', { category, name }),
  clear: () => api<LogClearResult>('/logs', 'DELETE'),
}
