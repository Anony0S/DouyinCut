import { readonly, ref } from 'vue'

export type Appearance = 'system' | 'light' | 'dark'
const storageKey = 'douyincut.appearance'
const preference = ref<Appearance>('system')
export const appearance = readonly(preference)
let systemTheme: MediaQueryList | undefined

function parse(value: string | null): Appearance {
  return value === 'light' || value === 'dark' ? value : 'system'
}
function apply() {
  document.documentElement.dataset.theme = preference.value === 'system'
    ? systemTheme?.matches ? 'dark' : 'light'
    : preference.value
}
export function setAppearance(value: Appearance) {
  preference.value = parse(value)
  try { localStorage.setItem(storageKey, preference.value) } catch { /* Keep the choice for this session when storage is unavailable. */ }
  apply()
}
export function startAppearance() {
  systemTheme = window.matchMedia('(prefers-color-scheme: dark)')
  try { preference.value = parse(localStorage.getItem(storageKey)) } catch { preference.value = 'system' }
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
    systemTheme?.removeEventListener('change', apply)
    window.removeEventListener('storage', sync)
  }
}
