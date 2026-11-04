export type StartupWindowSize = 'standard' | 'full-hd' | 'qhd'
export type WindowPreferences = { rememberPosition: boolean; startupSize: StartupWindowSize }

type TauriInternals = {
  invoke: (command: string, args?: Record<string, unknown>) => Promise<unknown>
}

const storageKey = 'douyincut.window-preferences'
export const defaultWindowPreferences: WindowPreferences = {
  rememberPosition: true,
  startupSize: 'standard',
}

function tauriInvoke() {
  return (window as Window & { __TAURI_INTERNALS__?: TauriInternals }).__TAURI_INTERNALS__?.invoke
}

function parse(value: unknown): WindowPreferences {
  if (!value || typeof value !== 'object') return { ...defaultWindowPreferences }
  const candidate = value as Partial<WindowPreferences>
  return {
    rememberPosition: typeof candidate.rememberPosition === 'boolean'
      ? candidate.rememberPosition
      : defaultWindowPreferences.rememberPosition,
    startupSize: candidate.startupSize === 'full-hd' || candidate.startupSize === 'qhd'
      ? candidate.startupSize
      : defaultWindowPreferences.startupSize,
  }
}

export async function loadWindowPreferences(): Promise<WindowPreferences> {
  const invoke = tauriInvoke()
  if (invoke) return parse(await invoke('get_window_preferences'))
  try {
    return parse(JSON.parse(localStorage.getItem(storageKey) || 'null'))
  } catch {
    return { ...defaultWindowPreferences }
  }
}

export async function saveWindowPreferences(value: WindowPreferences): Promise<WindowPreferences> {
  const preferences = parse(value)
  const invoke = tauriInvoke()
  if (invoke) {
    return parse(await invoke('set_window_preferences', preferences))
  }
  try {
    localStorage.setItem(storageKey, JSON.stringify(preferences))
  } catch {
    throw new Error('无法保存窗口设置。')
  }
  return preferences
}
