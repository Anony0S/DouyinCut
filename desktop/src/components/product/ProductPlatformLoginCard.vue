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
  <div>
    <div class="flex flex-wrap items-center justify-between gap-4">
      <div>
        <h2 class="text-lg font-semibold">星斗商品平台</h2>
        <p class="mt-2 text-sm text-base-content/60">登录后可在商品讲解列表中查询商品信息。</p>
      </div>
      <span v-if="status" class="badge badge-soft" :class="status.logged_in ? 'badge-success' : 'badge-neutral'">
        {{ status.logged_in ? '已登录' : '未登录' }}
      </span>
    </div>
    <p v-if="loading && !status" role="status" class="mt-6 text-sm text-base-content/60">正在读取登录状态…</p>
    <div v-if="error" role="alert" class="alert alert-soft alert-error mt-5 text-sm">
      <span>{{ error }}</span>
      <button type="button" class="btn btn-sm" :disabled="loading" @click="run(productPlatformApi.status)">重试</button>
    </div>
    <template v-if="status?.logged_in">
      <dl class="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <div><dt class="text-sm text-base-content/60">账号</dt><dd class="mt-1 font-medium">{{ status.member_name || '星斗用户' }}</dd></div>
        <div><dt class="text-sm text-base-content/60">手机号</dt><dd class="mt-1 font-medium">{{ status.mobile || '—' }}</dd></div>
        <div><dt class="text-sm text-base-content/60">最近验证</dt><dd class="mt-1 text-sm">{{ status.last_validated_at ? new Date(status.last_validated_at * 1000).toLocaleString() : '—' }}</dd></div>
      </dl>
      <label for="product-platform-scope" class="mb-2 mt-6 block text-sm">商品查询范围</label>
      <select v-if="status.scopes.length" id="product-platform-scope" class="select w-full sm:max-w-md"
        :value="status.main_ip_id" :disabled="loading" @change="selectScope">
        <option v-for="scope in status.scopes" :key="scope.id" :value="scope.id">{{ scope.name }}</option>
      </select>
      <p v-else class="alert alert-soft alert-warning text-sm" role="status">当前账号没有关联的商品查询范围，请先在星斗平台确认合作权限后重新登录。</p>
      <p class="mt-3 text-sm text-base-content/60">搜索在所选范围内进行；登录失效时会提示前往此处重新登录。</p>
      <div class="mt-6 flex flex-wrap justify-end gap-2">
        <button class="btn" :disabled="loading" @click="logout">退出登录</button>
        <button class="btn" :disabled="loading" @click="showLogin = true">重新登录</button>
      </div>
    </template>
    <div v-else-if="status" class="py-8 text-center">
      <p class="mb-5 text-sm text-base-content/60">尚未登录星斗商品平台</p>
      <button class="btn btn-primary" :disabled="loading" @click="showLogin = true">点击登录</button>
    </div>
    <ProductLoginDialog v-if="showLogin" :cooldown-until="cooldownUntil" @close="showLogin = false"
      @code-sent="cooldownUntil = $event" @logged-in="loggedIn" />
  </div>
</template>
