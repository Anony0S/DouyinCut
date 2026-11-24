<script setup lang="ts">
import { ref, watch } from 'vue'
import { productPlatformApi, type RoomPlatformBinding, type XingdongliStatus } from '../../services/productPlatform'

const binding = defineModel<RoomPlatformBinding | null>({ required: true })
const account = ref<XingdongliStatus>()
const loading = ref(false)
const error = ref('')

watch(() => binding.value?.platform, async (platform, _, onCleanup) => {
  if (platform !== 'xingdongli') return
  let stale = false
  onCleanup(() => { stale = true })
  account.value = undefined
  loading.value = true
  error.value = ''
  try {
    const status = await productPlatformApi.xingdongliStatus()
    if (stale) return
    account.value = status
    const current = binding.value
    if (current?.platform === 'xingdongli' && !status.scopes.some(scope => scope.id === current.ip_id)) {
      binding.value = { platform: 'xingdongli', ip_id: '' }
    }
  } catch (cause) {
    if (!stale) error.value = cause instanceof Error ? cause.message : '读取星动力签约达人失败'
  } finally {
    if (!stale) loading.value = false
  }
}, { immediate: true })

function selectPlatform(event: Event) {
  const platform = (event.target as HTMLSelectElement).value
  binding.value = platform === 'xingdou' || platform === 'daduoduo' || platform === 'xingdongli'
    ? { platform, ip_id: '' }
    : null
}
</script>

<template>
  <div class="grid gap-3">
    <label class="my-0! grid grid-cols-[4.5rem_minmax(0,1fr)] items-center gap-3">
      <span class="text-sm font-medium">商品平台</span>
      <select class="select select-sm w-full" :value="binding?.platform || ''" @change="selectPlatform">
        <option value="">不绑定</option>
        <option value="xingdou">星斗</option>
        <option value="daduoduo">达多多</option>
        <option value="xingdongli">星动力</option>
      </select>
    </label>
    <template v-if="binding?.platform === 'xingdongli'">
      <label class="my-0! grid grid-cols-[4.5rem_minmax(0,1fr)] items-center gap-3">
        <span class="text-sm font-medium">签约达人</span>
        <select v-model="binding.ip_id" class="select select-sm w-full" required :disabled="loading || !account?.scopes.length">
          <option value="" disabled>选择签约达人</option>
          <option v-for="scope in account?.scopes" :key="scope.id" :value="scope.id">{{ scope.name }} · {{ scope.id }}</option>
        </select>
      </label>
      <p v-if="loading" class="text-sm text-base-content/70" role="status">正在读取签约达人…</p>
      <p v-else-if="error" class="text-sm text-error" role="alert">{{ error }}</p>
      <p v-else-if="!account?.logged_in" class="text-sm text-base-content/70">请在云端设置的星动力平台填写 member-token。</p>
      <p v-else-if="!account.scopes.length" class="text-sm text-base-content/70">当前星动力账号暂无签约达人。</p>
    </template>
    <label v-if="binding?.platform === 'daduoduo'" class="my-0! grid grid-cols-[4.5rem_minmax(0,1fr)] items-center gap-3">
      <span class="text-sm font-medium">ipId</span>
      <input v-model.trim="binding.ip_id" class="input input-sm w-full" maxlength="128" required autocomplete="off" />
    </label>
  </div>
</template>
