export type UpdateStatus = {
  phase: 'idle' | 'checking' | 'available' | 'downloading' | 'ready' | 'installing' | 'error'
  currentVersion: string
  latestVersion: string | null
  notes: string
  hasUpdate: boolean
  downloadedBytes: number
  totalBytes: number
  message: string
}

export function desktopUpdatesAvailable(): boolean {
  return Boolean((window as Window & { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__)
}

export function updateCommand<T = void>(command: string): Promise<T> {
  const internals = (window as Window & {
    __TAURI_INTERNALS__?: { invoke: (command: string) => Promise<T> }
  }).__TAURI_INTERNALS__
  if (!internals) return Promise.reject(new Error('请在 Windows 桌面应用中使用在线更新'))
  return internals.invoke(command)
}

export function assertNoRecording(monitors: { record_requested: boolean; sessions: { status: string }[] }[]): void {
  if (monitors.some(monitor => monitor.record_requested || monitor.sessions.some(session => session.status === 'recording'))) {
    throw new Error('正在录制或准备录制，请先在直播监控中停止录制，再安装更新。')
  }
}
