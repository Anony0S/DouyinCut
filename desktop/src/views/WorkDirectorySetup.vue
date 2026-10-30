<script setup lang="ts">
import { onMounted, reactive } from 'vue'
import { createWorkDirectoryFlow, type DirectoryState } from '../work-directory'

declare global {
  interface Window {
    __TAURI_INTERNALS__: { invoke: (command: string, args: Record<string, unknown>) => Promise<DirectoryState> }
  }
}
const flow = createWorkDirectoryFlow((command, args) => window.__TAURI_INTERNALS__.invoke(command, args))
flow.view = reactive(flow.view)
const view = flow.view

// Check immediately before mounting to avoid flash
onMounted(async () => {
  await flow.run('inspect')
  // If already configured and opened, no need to show this screen
})
</script>

<template>
  <!-- Only show setup UI if selection or repair is required -->
  <div v-if="view.state === 'selection_required' || view.state === 'repair_required'" class="min-h-screen bg-base-200 px-6 py-16 text-base-content">
    <section class="card card-border mx-auto max-w-2xl bg-base-100 shadow-sm" aria-labelledby="setup-title">
      <div class="card-body gap-6 p-8">
        <div>
          <p class="mb-2 text-sm text-base-content/60">DouyinCut · 工作数据</p>
          <h1 id="setup-title" class="card-title text-2xl">{{ view.state === 'repair_required' ? '恢复工作目录访问' : '选择工作数据目录' }}</h1>
        </div>
        <p>项目、数据库、录制、分析、预览和导出统一保存在此目录，与程序文件分开。</p>
        <p class="text-sm text-base-content/70">请选择新的空目录，或重新关联原位置的当前格式工作数据。支持中文与空格路径。选定后固定使用原盘符和路径，本版不支持搬迁、改名或多工作区切换。</p>
        <div v-if="view.path" class="rounded-box bg-base-200 p-4">
          <p class="mb-2 text-sm text-base-content/60">原工作目录</p>
          <p class="break-all font-mono text-sm">{{ view.path }}</p>
        </div>
        <div v-if="view.message" class="alert" :class="{ 'alert-error': view.state === 'repair_required' }" role="status">
          <span class="whitespace-pre-line break-all">{{ view.message }}</span>
        </div>
        <p v-if="view.busy" role="status">正在验证目录并准备工作台，请稍候……</p>
        <div class="card-actions justify-end">
          <button v-if="view.state === 'repair_required'" class="btn" :disabled="view.busy" @click="flow.run('inspect')">重试原位置</button>
          <button class="btn" :disabled="view.busy" @click="flow.run('select')">{{ view.path ? '定位原工作目录' : '选择目录' }}</button>
        </div>
        <p class="text-xs text-base-content/60">取消选择将停留在此页面。目录验证通过前不会启动 API、任务或直播监控。</p>
      </div>
    </section>
  </div>
</template>
