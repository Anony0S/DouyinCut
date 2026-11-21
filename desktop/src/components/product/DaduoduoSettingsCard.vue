<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { productPlatformApi, type DaduoduoSettings } from '../../services/productPlatform'

const settings = ref<DaduoduoSettings>()
const error = ref('')

onMounted(async () => {
  try {
    settings.value = await productPlatformApi.daduoduoSettings()
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '读取达多多目录状态失败'
  }
})
</script>

<template>
  <div class="card-body">
    <h2 class="card-title">达多多</h2>
    <p class="text-sm text-base-content/70">打开直播间的齿轮菜单，点击「绑定平台」，选择达多多并填写该直播间的 ipId。</p>
    <RouterLink class="btn btn-sm self-start" to="/live-monitoring">前往直播监控</RouterLink>
    <ul v-if="settings?.rooms.length" class="mt-3 grid gap-2 border-t border-base-300 pt-3 text-sm">
      <li v-for="room in settings.rooms" :key="room.id">
        {{ room.name }} · {{ room.synced_at ? `${room.count} 条 · ${new Date(room.synced_at * 1000).toLocaleString()}` : '尚未同步' }}
      </li>
    </ul>
    <p v-if="error" class="text-sm text-error" role="alert">{{ error }}</p>
  </div>
</template>
