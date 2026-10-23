import { inject, provide, ref, type InjectionKey } from 'vue'
import { useSessionStore } from '../stores/session'
import { useProjectStore } from '../stores/project'
import { projectsApi } from '../services/projects'
import type { Workspace } from '../workspace'

const key: InjectionKey<{chooseNewVideo:()=>Promise<void>}> = Symbol('video-import')
export function provideVideoImport(workspace: Workspace) {
  const session=useSessionStore(),project=useProjectStore(),fileInput=ref<HTMLInputElement|null>(null)
  async function chooseNewVideo() {
    await session.act(async()=>{project.checkSwitch();await project.persistDraft();fileInput.value?.click()})
  }
  async function selectFile(event: Event) {
    const input=event.target as HTMLInputElement,file=input.files?.[0]
    if(!file) return
    try {
      await session.act(async()=>{
        project.checkSwitch();await project.persistDraft()
        session.message='正在将视频保存到本机素材目录…'
        const job=await projectsApi.upload(file)
        session.pendingImportId=job.id;session.message='视频已保存，正在读取素材信息'
        await workspace.refresh()
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
