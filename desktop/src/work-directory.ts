export type DirectoryState = {
  state: 'selection_required' | 'repair_required' | 'ready' | 'opened'
  path: string | null
  message: string
}
type Action = 'inspect' | 'select' | 'open'
type Invoke = (command: string, args: { action: Action }) => Promise<DirectoryState>

export function createWorkDirectoryFlow(invoke: Invoke) {
  const flow = {
    view: { state: 'selection_required', path: null, message: '', busy: false } as DirectoryState & { busy: boolean },
    async run(action: Action) {
      if (flow.view.busy) return
      flow.view.busy = true
      try {
        Object.assign(flow.view, await invoke('workspace_action', { action }))
        if (flow.view.state === 'ready') Object.assign(flow.view, await invoke('workspace_action', { action: 'open' }))
      } catch (error) {
        flow.view.state = 'repair_required'
        flow.view.message = error instanceof Error ? error.message : String(error)
      } finally {
        flow.view.busy = false
      }
    },
  }
  return flow
}
