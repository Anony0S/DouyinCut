<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { isXingdongliAuthError, productPlatformApi, type XingdongliStatus } from '../../services/productPlatform'

const status = ref<XingdongliStatus>()
const token = ref('')
const busy = ref(false)
const error = ref('')

async function run(action: () => Promise<XingdongliStatus>, refresh = false) {
  if (busy.value) return
  busy.value = true
  error.value = ''
  try {
    status.value = await action()
    token.value = ''
  } catch (cause) {
    if (refresh && isXingdongliAuthError(cause)) status.value = undefined
    error.value = cause instanceof Error ? cause.message : '读取星动力状态失败'
  } finally {
    busy.value = false
  }
}

function connect() {
  const value = token.value.trim()
  if (value) void run(() => productPlatformApi.connectXingdongli(value))
}

onMounted(() => { void run(productPlatformApi.xingdongliStatus) })
</script>

<template>
  <div class="card-body w-full max-w-4xl gap-5 p-5 sm:p-7">
    <div class="flex flex-wrap items-center justify-between gap-4">
      <div>
        <h2 class="text-xl! font-semibold">星动力</h2>
        <p class="mt-1 text-sm text-base-content/70">验证小程序 member-token 后，可在直播间绑定签约达人。</p>
      </div>
      <span v-if="status" class="badge badge-soft" :class="status.logged_in ? 'badge-success' : 'badge-neutral'">
        {{ status.logged_in ? '已登录' : '未登录' }}
      </span>
    </div>
    <form class="grid gap-2 rounded-box bg-base-200 p-4" @submit.prevent="connect">
      <label for="xingdongli-member-token" class="m-0 text-sm font-medium">member-token</label>
      <div class="flex flex-col gap-2 sm:flex-row">
        <input id="xingdongli-member-token" v-model="token" type="password" autocomplete="off"
          spellcheck="false" maxlength="4096" class="input w-full min-w-0 sm:flex-1"
          :placeholder="status?.logged_in ? '粘贴新 token 以更换账号' : '粘贴小程序 token'"
          :disabled="busy" />
        <button class="btn btn-primary shrink-0" type="submit" :disabled="busy || !token.trim()">验证并保存</button>
      </div>
    </form>
    <div v-if="error" class="alert alert-soft alert-error text-sm" role="alert">{{ error }}</div>
    <p v-if="busy" class="text-sm text-base-content/70" role="status">正在处理星动力账号信息…</p>
    <div v-if="status?.logged_in" class="grid gap-4">
      <dl class="grid gap-4 sm:grid-cols-2">
        <div><dt class="text-sm text-base-content/60">账号</dt><dd class="mt-1 font-medium">{{ status.member_name || '星动力会员' }}</dd></div>
        <div v-if="status.mobile"><dt class="text-sm text-base-content/60">手机号</dt><dd class="mt-1 font-medium">{{ status.mobile }}</dd></div>
      </dl>
      <div>
        <h3 class="text-sm font-medium">签约达人</h3>
        <ul v-if="status.scopes.length" class="mt-2 divide-y divide-base-300 rounded-box border border-base-300 text-sm">
          <li v-for="scope in status.scopes" :key="scope.id" class="flex flex-wrap items-center justify-between gap-2 px-4 py-2.5">
            <strong class="break-all font-medium">{{ scope.name }}</strong>
            <span class="break-all text-base-content/70">ipId {{ scope.id }}</span>
          </li>
        </ul>
        <p v-else class="mt-2 text-sm text-base-content/70">当前账号暂无签约达人。</p>
      </div>
      <div class="flex flex-wrap gap-2 border-t border-base-300 pt-4">
        <button type="button" class="btn btn-sm" :disabled="busy"
          @click="run(productPlatformApi.refreshXingdongli, true)">刷新账号和达人</button>
        <button type="button" class="btn btn-sm btn-ghost" :disabled="busy"
          @click="run(productPlatformApi.logoutXingdongli)">退出本机登录</button>
      </div>
    </div>
  </div>
</template>
