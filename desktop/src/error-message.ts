export function errorMessage(cause: unknown): string {
  if (cause instanceof Error) return cause.message
  if (typeof cause === 'string') return cause.trim() || '操作失败'
  if (cause && typeof cause === 'object' && 'message' in cause &&
      typeof cause.message === 'string' && cause.message.trim()) return cause.message.trim()
  if (cause && typeof cause === 'object') {
    try {
      const detail = JSON.stringify(cause)
      if (detail && detail !== '{}') return detail.slice(0, 500)
    } catch { /* Fall through for non-serializable bridge errors. */ }
    return Object.prototype.toString.call(cause)
  }
  if (cause !== undefined && cause !== null) return String(cause)
  if (cause === null) return '桌面退出控制返回了空错误'
  return '操作失败'
}
