<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useRoute } from 'vue-router'
import { duration, seconds } from '../api'
import { useSessionStore } from '../stores/session'
import { useProjectsStore, projectFilter } from '../stores/projects'
import { projectStatusLabels, updatedTime } from '../presentation'
import { useWorkspace } from '../workspace'
import { useVideoImport } from '../composables/useVideoImport'
import ConfirmPopover from '../components/ConfirmPopover.vue'
const session=useSessionStore(), list=useProjectsStore(), route=useRoute(), workspace=useWorkspace()
const {busy,connected,pendingImportId}=storeToRefs(session)
const {projectPage,loading:projectLoading}=storeToRefs(list)
const projectSearch=ref(''),projectStatus=ref('all')
const appliedSearch=computed(()=>list.filter.search)
const {chooseNewVideo}=useVideoImport()
function projectStatusBadgeClass(status: string) {
  return ({completed:'badge-success',processing:'badge-info',failed:'badge-error',paused:'badge-warning'} as Record<string,string>)[status]||'badge-ghost'
}
async function filterProjects() {await workspace.goProjects({page:1,search:projectSearch.value.trim(),status:projectStatus.value})}
async function changeProjectPage(page: number) {await workspace.goProjects({...list.filter,page})}
const openProject=workspace.openProject
async function deleteProject(id: string) {
  await session.act(async()=>{const result=await list.remove(id);session.message=result.cleanup_pending.length?'项目记录已删除，部分本地文件暂时无法清理；OSS 临时音频未删除':'视频项目及其本地产物已删除；OSS 临时音频未删除'})
}
watch(()=>route.query,()=>{
  const filter=projectFilter(route.query)
  projectSearch.value=filter.search;projectStatus.value=filter.status
  void workspace.refreshProjects(filter)
},{immediate:true})
</script>
<template>
  <section class="card card-border bg-base-100 w-full projects-panel">
    <div class="card-body">
      <div class="projects-toolbar">
        <form class="project-filters" @submit.prevent="filterProjects">
          <input v-model="projectSearch" class="input input-md" aria-label="搜索视频或商品名称" placeholder="搜索视频名称、商品名称" maxlength="200"/>
          <select v-model="projectStatus" class="select" aria-label="项目状态" @change="filterProjects">
            <option value="all">全部状态</option>
            <option v-for="(label,value) in projectStatusLabels" :key="value" :value="value">{{ label }}</option>
          </select>
          <button class="btn btn-md" :disabled="busy||projectLoading">搜索</button>
        </form>
        <button class="btn btn-primary" :disabled="busy||!connected||Boolean(pendingImportId)" @click="chooseNewVideo">
          {{ pendingImportId?'正在导入…':'＋ 新建视频项目' }}
        </button>
      </div>
      <p v-if="pendingImportId" class="hint" role="status">视频正在导入，完成后会自动进入素材与粗剪。</p>
      <div class="project-table-scroll" :aria-busy="projectLoading">
        <table class="table table-sm table-pin-rows">
          <thead>
            <tr>
              <th>视频 / 商品</th><th>原视频时长</th><th>处理状态</th><th>成片时长</th><th>最近更新</th><th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="project in projectPage.items" :key="project.id">
              <td class="project-name-cell">
                <strong :title="project.name">{{ project.name }}</strong>
                <span>{{ project.product_name||'尚未填写商品名称' }}</span>
              </td>
              <td>{{ duration(project.duration_us) }}</td>
              <td><span :class="['badge',projectStatusBadgeClass(project.status)]">{{ projectStatusLabels[project.status] }}</span></td>
              <td>{{ project.output_duration_us===null?'—':`${seconds(project.output_duration_us)} 秒` }}</td>
              <td>{{ updatedTime(project.updated_at) }}</td>
              <td class="project-actions">
                <button class="btn btn-sm mr-2" :disabled="busy" @click="openProject(project.id)">查看 / 继续编辑</button>
                <ConfirmPopover
                  :title="`删除项目“${project.name}”？`"
                  message="将删除本机导入副本、项目资料、ASR 对话、粗剪方案及本地产物；原始视频文件和 OSS 临时 ASR 音频会保留。此操作不可撤销。"
                  :confirm-action="() => deleteProject(project.id)"
                  :disabled="busy || projectLoading"
                  v-slot="{ open, expanded, popoverId }">
                  <button
                    class="btn btn-error btn-outline btn-sm"
                    :disabled="busy || projectLoading"
                    :aria-expanded="expanded"
                    :aria-controls="popoverId"
                    aria-haspopup="dialog"
                    @click="open">
                    删除
                  </button>
                </ConfirmPopover>
              </td>
            </tr>
          </tbody>
        </table>
        <div v-if="!projectPage.items.length" class="empty">
          {{ projectLoading?'正在加载视频项目…':appliedSearch||projectStatus!=='all'?'没有符合条件的视频项目':'暂无视频项目，点击“新建视频项目”导入视频。' }}
        </div>
      </div>
      <div class="project-pagination">
        <span>共 {{ projectPage.total }} 条 · 每页 20 条</span>
        <div class="join">
          <button class="btn btn-sm join-item" :disabled="busy||projectLoading||projectPage.page<=1" @click="changeProjectPage(projectPage.page-1)">上一页</button>
          <button class="btn btn-sm btn-active join-item" aria-current="page" disabled>第 {{ projectPage.page }} / {{ projectPage.pages }} 页</button>
          <button class="btn btn-sm join-item" :disabled="busy||projectLoading||projectPage.page>=projectPage.pages" @click="changeProjectPage(projectPage.page+1)">下一页</button>
        </div>
      </div>
    </div>
  </section>
</template>
