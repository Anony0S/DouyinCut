<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { productPlatformApi, type ProductPlatformStatus } from '../../services/productPlatform'
import { useSessionStore } from '../../stores/session'
import ProductLoginDialog from './ProductLoginDialog.vue'

const session = useSessionStore()
const status = ref<ProductPlatformStatus>()
const error = ref(''), loading = ref(false), showLogin = ref(false), cooldownUntil = ref(0)
let disposed = false
async function run(action: () => Promise<ProductPlatformStatus>) {
  if (loading.value) return
  loading.value = true; error.value = ''
  try {
    const result = await action()
    if (!disposed) status.value = result
  } catch (cause) {
    if (!disposed) error.value = cause instanceof Error ? cause.message : '读取星斗登录状态失败'
  } finally { if (!disposed) loading.value = false }
}
function loggedIn(value: ProductPlatformStatus) {
  status.value = value; showLogin.value = false
  session.message = '星斗商品平台登录成功'
}
async function logout() {
  await run(productPlatformApi.logout)
  if (!disposed && !error.value) session.message = '已退出星斗商品平台的本机登录'
}
function selectScope(event: Event) {
  const value = (event.target as HTMLSelectElement).value
  void run(() => productPlatformApi.selectScope(value))
}
onMounted(() => { void run(productPlatformApi.status) })
onUnmounted(() => { disposed = true })
</script>

<template>
  <div class="card-body w-full max-w-4xl gap-5 p-5 sm:p-7">
    <div class="flex flex-wrap items-center justify-between gap-4">
      <div>
        <h2 class="text-xl! font-semibold">星斗</h2>
        <p class="mt-1 text-sm text-base-content/70">使用手机号登录，选择合作范围后查询商品。</p>
      </div>
      <span v-if="status" class="badge badge-soft" :class="status.logged_in ? 'badge-success' : 'badge-neutral'">
        {{ status.logged_in ? '已登录' : '未登录' }}
      </span>
    </div>
    <p v-if="loading && !status" role="status" class="text-sm text-base-content/70">正在读取登录状态…</p>
    <div v-if="error" role="alert" class="alert alert-soft alert-error text-sm">
      <span>{{ error }}</span>
      <button type="button" class="btn btn-sm" :disabled="loading" @click="run(productPlatformApi.status)">重试</button>
    </div>
    <template v-if="status?.logged_in">
      <dl class="grid gap-4 rounded-box bg-base-200 p-4 sm:grid-cols-3">
        <div><dt class="text-sm text-base-content/60">账号</dt><dd class="mt-1 font-medium">{{ status.member_name || '星斗用户' }}</dd></div>
        <div><dt class="text-sm text-base-content/60">手机号</dt><dd class="mt-1 font-medium">{{ status.mobile || '—' }}</dd></div>
        <div><dt class="text-sm text-base-content/60">最近验证</dt><dd class="mt-1 text-sm">{{ status.last_validated_at ? new Date(status.last_validated_at * 1000).toLocaleString() : '—' }}</dd></div>
      </dl>
      <label v-if="status.scopes.length" class="m-0 grid gap-2 text-sm font-medium">
        <span>商品查询范围</span>
        <select id="product-platform-scope" class="select w-full sm:max-w-md"
          :value="status.main_ip_id" :disabled="loading" @change="selectScope">
          <option v-for="scope in status.scopes" :key="scope.id" :value="scope.id">{{ scope.name }}</option>
        </select>
      </label>
      <p v-else class="alert alert-soft alert-warning text-sm" role="status">当前账号没有关联的商品查询范围，请先在星斗平台确认合作权限后重新登录。</p>
      <div class="flex flex-wrap gap-2 border-t border-base-300 pt-4">
        <button class="btn btn-sm" :disabled="loading" @click="showLogin = true">重新登录</button>
        <button class="btn btn-ghost btn-sm" :disabled="loading" @click="logout">退出登录</button>
      </div>
    </template>
    <div v-else-if="status" class="rounded-box bg-base-200 p-4">
      <button class="btn btn-primary" :disabled="loading" @click="showLogin = true">点击登录</button>
    </div>
    <ProductLoginDialog v-if="showLogin" :cooldown-until="cooldownUntil" @close="showLogin = false"
      @code-sent="cooldownUntil = $event" @logged-in="loggedIn" />
  </div>
</template>
