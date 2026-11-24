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
  <div class="card-body w-full max-w-4xl gap-5 p-5 sm:p-7">
    <div class="flex flex-wrap items-center justify-between gap-4">
      <div>
        <h2 class="text-xl! font-semibold">达多多</h2>
        <p class="mt-1 text-sm text-base-content/70">在直播间的「绑定平台」中填写 ipId。</p>
      </div>
      <RouterLink class="btn btn-sm" to="/live-monitoring">前往直播监控</RouterLink>
    </div>
    <div v-if="error" class="alert alert-soft alert-error text-sm" role="alert">{{ error }}</div>
    <p v-else-if="!settings" class="text-sm text-base-content/70" role="status">正在读取商品目录…</p>
    <div v-else-if="settings.rooms.length" class="overflow-x-auto rounded-box border border-base-300">
      <table class="table table-sm">
        <thead class="bg-base-200">
          <tr><th scope="col">直播间</th><th scope="col">商品数</th><th scope="col">最近同步</th></tr>
        </thead>
        <tbody>
          <tr v-for="room in settings.rooms" :key="room.id">
            <th scope="row" class="font-medium">{{ room.name }}</th>
            <td>{{ room.synced_at ? room.count : '—' }}</td>
            <td class="whitespace-nowrap">{{ room.synced_at ? new Date(room.synced_at * 1000).toLocaleString() : '尚未同步' }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    <p v-else class="rounded-box bg-base-200 p-4 text-sm text-base-content/70">暂无直播间绑定达多多。</p>
  </div>
</template>
