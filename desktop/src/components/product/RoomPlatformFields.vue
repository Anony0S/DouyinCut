<script setup lang="ts">
import type { RoomPlatformBinding } from '../../services/productPlatform'

const binding = defineModel<RoomPlatformBinding | null>({ required: true })
function selectPlatform(event: Event) {
  const platform = (event.target as HTMLSelectElement).value
  binding.value = platform === 'xingdou' || platform === 'daduoduo'
    ? { platform, ip_id: '' }
    : null
}
</script>

<template>
  <div class="grid gap-3">
    <label class="grid gap-1.5">
      <span class="text-sm font-medium">商品平台</span>
      <select class="select select-sm w-full" :value="binding?.platform || ''" @change="selectPlatform">
        <option value="">不绑定</option>
        <option value="xingdou">星斗</option>
        <option value="daduoduo">达多多</option>
      </select>
    </label>
    <label v-if="binding?.platform === 'daduoduo'" class="grid gap-1.5">
      <span class="text-sm font-medium">ipId</span>
      <input v-model.trim="binding.ip_id" class="input input-sm w-full" maxlength="128" required autocomplete="off" />
    </label>
  </div>
</template>
