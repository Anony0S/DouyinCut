import { readonly, ref } from 'vue'

export type Appearance = 'system' | 'light' | 'dark'
const storageKey = 'douyincut.appearance'
const preference = ref<Appearance>('system')
export const appearance = readonly(preference)
let systemTheme: MediaQueryList | undefined
let preferenceVersion = 0

type TauriInvoke = (command: string, args?: Record<string, unknown>) => Promise<unknown>

function tauriInvoke(): TauriInvoke | undefined {
  return (window as Window & { __TAURI_INTERNALS__?: { invoke?: TauriInvoke } })
    .__TAURI_INTERNALS__?.invoke
}

function parse(value: unknown): Appearance {
  return value === 'light' || value === 'dark' ? value : 'system'
}

function readLocalPreference(): Appearance {
  try { return parse(localStorage.getItem(storageKey)) } catch { return 'system' }
}

function writeLocalPreference(value: Appearance) {
  try { localStorage.setItem(storageKey, value) } catch { /* Keep the choice for this session when storage is unavailable. */ }
}

function apply() {
  document.documentElement.dataset.theme = preference.value === 'system'
    ? systemTheme?.matches ? 'dark' : 'light'
    : preference.value
}
export function setAppearance(value: Appearance) {
  preference.value = parse(value)
  preferenceVersion += 1
  const invoke = tauriInvoke()
  if (invoke) {
    void invoke('set_appearance_preference', { appearance: preference.value }).catch(() => {
      writeLocalPreference(preference.value)
    })
  } else {
    writeLocalPreference(preference.value)
  }
  apply()
}
export function startAppearance() {
  systemTheme = window.matchMedia('(prefers-color-scheme: dark)')
  const invoke = tauriInvoke()
  const initialVersion = preferenceVersion
  let stopped = false
  if (invoke) {
    void invoke('get_appearance_preference').then(value => {
      if (!stopped && preferenceVersion === initialVersion) {
        preference.value = parse(value)
        apply()
      }
    }).catch(() => {
      if (!stopped && preferenceVersion === initialVersion) {
        preference.value = readLocalPreference()
        apply()
      }
    })
  } else {
    preference.value = readLocalPreference()
  }
  apply()
  const sync = (event: StorageEvent) => {
    if (event.key === storageKey || event.key === null) {
      preference.value = parse(event.newValue)
      apply()
    }
  }
  systemTheme.addEventListener('change', apply)
  window.addEventListener('storage', sync)
  return () => {
    stopped = true
    systemTheme?.removeEventListener('change', apply)
    window.removeEventListener('storage', sync)
  }
}
