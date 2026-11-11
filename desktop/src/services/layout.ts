import { api } from '../api'

/** `left_width` is the left pane width in pixels; null means the default width. */
export type WorkspaceLayout = { left_width: number | null }

export const workspaceLayoutApi = {
  get: () => api<WorkspaceLayout>('/settings/workspace-layout'),
  save: (leftWidth: number | null) => api<WorkspaceLayout>('/settings/workspace-layout', 'PUT', { left_width: leftWidth }),
}
