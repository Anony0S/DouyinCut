type ExitGuard = () => boolean

const guards = new Set<ExitGuard>()

export function registerExitGuard(guard: ExitGuard) {
  guards.add(guard)
  return () => guards.delete(guard)
}

export function hasUnsavedChanges() {
  return [...guards].some(guard => guard())
}
