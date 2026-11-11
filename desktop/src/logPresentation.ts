import { kindLabels } from './presentation'
import type { LogChunk, LogFile } from './services/logs'

export type LogLevel = 'error' | 'warning' | 'info' | ''
export type LogLine = { index: number; text: string; level: LogLevel }
export type LogGroup = { key: string; title: string; subtitle: string; files: LogFile[] }

/** Text kept in the viewer while following a growing file. */
export const VIEW_LIMIT = 1_000_000

const RECORD = /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} ([A-Z]+)/
// Lines written before the shared format existed carry no time or level.
const UNFORMATTED_ERROR = /^(ERROR|CRITICAL)\b|^Traceback \(most recent call last\)/

export const fileKey = (file: Pick<LogFile, 'category' | 'name'>) => `${file.category}/${file.name}`

export function fileLabel(file: LogFile) {
  if (file.category === 'runtime') return file.name.includes('.previous.') ? '上一份运行日志' : '当前运行日志'
  const part = (file.group ? file.name.slice(file.name.indexOf(file.group) + file.group.length + 1) : file.name).replace(/\.log$/, '')
  if (/^\d+$/.test(part)) return `片段 ${Number(part) + 1}`
  if (part === 'concat') return '拼接'
  const jianying = /^jianying-(\d+)$/.exec(part)
  return jianying ? `剪映片段 ${Number(jianying[1]) + 1}` : part
}

/** Task logs grouped by the job or product clip that produced them, in the order the files arrive (newest first). */
export function groupTaskLogs(files: LogFile[]): LogGroup[] {
  const groups = new Map<string, LogGroup>()
  for (const file of files) {
    if (file.category !== 'task') continue
    const key = file.group ?? ''
    let group = groups.get(key)
    if (!group) {
      const title = file.group_type === 'job' ? (file.job_kind ? kindLabels[file.job_kind] || file.job_kind : '已删除的任务')
        : file.group_type === 'product_clip' ? `商品片段编码${file.product_name ? ` · ${file.product_name}` : ''}` : '其他日志'
      group = { key, title, subtitle: file.group ? `${file.group_type === 'job' ? '任务' : '片段'} ${file.group.slice(-10)}` : '', files: [] }
      groups.set(key, group)
    }
    group.files.push(file)
  }
  for (const group of groups.values()) group.files.sort((a, b) => a.name.localeCompare(b.name, 'en', { numeric: true }))
  return [...groups.values()]
}

/** One entry per line; traceback and other continuation lines take the level of the record they follow. */
export function classifyLines(text: string): LogLine[] {
  const lines = text.split(/\r?\n/)
  if (lines.at(-1) === '') lines.pop()
  let level: LogLevel = ''
  return lines.map((line, index) => {
    const record = RECORD.exec(line)
    if (record) level = ['ERROR', 'CRITICAL'].includes(record[1]) ? 'error' : record[1] === 'WARNING' ? 'warning' : 'info'
    else if (UNFORMATTED_ERROR.test(line)) level = 'error'
    return { index, text: line, level }
  })
}

/** Append a follow-up read, dropping the oldest whole lines once the viewer holds more than `limit` characters. */
export function appendChunk(current: string, truncated: boolean, chunk: LogChunk, limit = VIEW_LIMIT) {
  let text = chunk.reset ? chunk.text : current + chunk.text
  truncated = chunk.reset ? chunk.truncated : truncated
  if (text.length > limit) {
    // Start one character early so a line that begins exactly at the limit is kept whole.
    const cut = text.indexOf('\n', text.length - limit - 1)
    text = cut < 0 ? text.slice(-limit) : text.slice(cut + 1)
    truncated = true
  }
  return { text, truncated }
}
