import { inject, provide, ref, type InjectionKey } from 'vue'
import { useSessionStore } from '../stores/session'
import { useProjectStore } from '../stores/project'
import { projectsApi } from '../services/projects'
import type { Job } from '../api'
import type { Workspace } from '../workspace'

const key: InjectionKey<{chooseNewVideo:()=>Promise<void>}> = Symbol('video-import')
export function provideVideoImport(workspace: Workspace) {
  const session=useSessionStore(),project=useProjectStore(),fileInput=ref<HTMLInputElement|null>(null)
  async function importVideo(submit: () => Promise<Job>) {
    session.message='正在将视频保存到本机素材目录…'
    const job=await submit()
    session.pendingImportId=job.id;session.message='视频已保存，正在读取素材信息'
    await workspace.refresh()
  }
  async function chooseNewVideo() {
    await session.act(async()=>{
      project.checkSwitch();await project.persistDraft()
      const native=(window as Window & {__TAURI_INTERNALS__?: {invoke: (command: string) => Promise<string | null>}}).__TAURI_INTERNALS__
      if (!native) {
        fileInput.value?.click()
        return
      }
      const path=await native.invoke('pick_source_video')
      if (path) await importVideo(()=>projectsApi.importLocal(path))
    })
    if(session.pendingImportId) await workspace.refresh().catch(session.report)
  }
  async function selectFile(event: Event) {
    const input=event.target as HTMLInputElement,file=input.files?.[0]
    if(!file) return
    try {
      await session.act(async()=>{
        project.checkSwitch();await project.persistDraft()
        await importVideo(()=>projectsApi.upload(file))
      })
      if(session.pendingImportId) await workspace.refresh().catch(session.report)
    } finally {input.value=''}
  }
  provide(key,{chooseNewVideo})
  return {fileInput,chooseNewVideo,selectFile}
}
export function useVideoImport() {
  const importer=inject(key)
  if(!importer) throw new Error('视频导入服务未初始化')
  return importer
}
