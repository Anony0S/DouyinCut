<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, useId } from 'vue'
import { openUrl } from '@tauri-apps/plugin-opener'
import { productPlatformApi, type ProductPlatformStatus } from '../../services/productPlatform'

const props = defineProps<{ cooldownUntil: number }>()
const emit = defineEmits<{
  close: []
  loggedIn: [status: ProductPlatformStatus]
  codeSent: [until: number]
}>()
const dialog = ref<HTMLDialogElement>()
const id = useId()
const mobile = ref(''), code = ref(''), error = ref(''), notice = ref('')
const pending = ref(false), sending = ref(false), accepted = ref(false)
const now = ref(Date.now())
const secondsLeft = computed(() => Math.max(0, Math.ceil((props.cooldownUntil - now.value) / 1000)))
const validMobile = computed(() => /^1[2-9]\d{9}$/.test(mobile.value.trim()))
const working = computed(() => pending.value || sending.value)
let disposed = false
let timer: ReturnType<typeof setInterval>
async function openAgreement(event: MouseEvent, type: '9' | '10') {
  if (!('__TAURI_INTERNALS__' in window)) return
  event.preventDefault()
  try { await openUrl(`https://e.reduxingxuan.com/treaty/index?type=${type}`) }
  catch { if (!disposed) error.value = '无法打开平台协议，请稍后重试' }
}
function close() {
  if (working.value) return
  mobile.value = ''; code.value = ''
  emit('close')
}
async function sendCode() {
  if (!validMobile.value || working.value || secondsLeft.value || !accepted.value) return
  sending.value = true; error.value = ''; notice.value = ''
  try {
    const result = await productPlatformApi.sendCode(mobile.value.trim())
    if (disposed) return
    now.value = Date.now()
    emit('codeSent', now.value + result.retry_after_seconds * 1000)
    notice.value = '验证码已发送，请查看手机短信'
  } catch (cause) {
    if (!disposed) error.value = cause instanceof Error ? cause.message : '验证码发送失败，请重试'
  } finally { if (!disposed) sending.value = false }
}
async function login() {
  if (!validMobile.value || !/^\d{4,8}$/.test(code.value.trim()) || !accepted.value || working.value) return
  pending.value = true; error.value = ''; notice.value = ''
  try {
    const status = await productPlatformApi.login(mobile.value.trim(), code.value.trim())
    if (!disposed) {
      mobile.value = ''; code.value = ''
      emit('loggedIn', status)
    }
  } catch (cause) {
    if (!disposed) error.value = cause instanceof Error ? cause.message : '登录失败，请重试'
  } finally { if (!disposed) pending.value = false }
}
onMounted(() => {
  dialog.value?.showModal()
  timer = setInterval(() => { now.value = Date.now() }, 1000)
})
onUnmounted(() => {
  disposed = true; clearInterval(timer)
  mobile.value = ''; code.value = ''
  dialog.value?.close()
})
</script>

<template>
  <Teleport to="body">
    <dialog ref="dialog" class="modal" :aria-labelledby="`${id}-title`" @cancel.prevent="close">
      <div class="modal-box max-w-md">
        <div class="flex items-start justify-between gap-4">
          <div>
            <h2 :id="`${id}-title`" class="text-lg font-semibold">登录星斗商品平台</h2>
            <p class="mt-2 text-sm text-base-content/60">使用星斗账号的手机号和短信验证码登录。</p>
          </div>
          <button type="button" class="btn btn-ghost btn-sm btn-square shrink-0" aria-label="关闭登录弹窗" :disabled="working" @click="close">✕</button>
        </div>
        <form class="mt-6" @submit.prevent="login">
          <label :for="`${id}-mobile`" class="mb-2 block text-sm">手机号</label>
          <input :id="`${id}-mobile`" v-model="mobile" class="input w-full" type="tel" inputmode="numeric" autocomplete="tel"
            maxlength="11" pattern="1[2-9][0-9]{9}" required autofocus :disabled="working" placeholder="请输入手机号" />
          <label :for="`${id}-code`" class="mb-2 mt-4 block text-sm">短信验证码</label>
          <div class="flex items-center gap-2">
            <input :id="`${id}-code`" v-model="code" class="input min-w-0 flex-1" type="text" inputmode="numeric" autocomplete="one-time-code"
              maxlength="8" pattern="[0-9]{4,8}" required :disabled="working" placeholder="请输入验证码" />
            <button type="button" class="btn shrink-0" :disabled="!validMobile || working || secondsLeft > 0 || !accepted" @click="sendCode">
              {{ sending ? '发送中…' : secondsLeft ? `${secondsLeft} 秒后重发` : '获取验证码' }}
            </button>
          </div>
          <label class="mt-4 flex items-start gap-2 text-sm text-base-content/70">
            <input v-model="accepted" class="checkbox checkbox-sm mt-0.5 shrink-0" type="checkbox" :disabled="working" />
            <span>我已阅读并同意星斗平台的
              <a class="link" href="https://e.reduxingxuan.com/treaty/index?type=9" target="_blank" rel="noopener noreferrer" @click="openAgreement($event, '9')">用户协议</a>和
              <a class="link" href="https://e.reduxingxuan.com/treaty/index?type=10" target="_blank" rel="noopener noreferrer" @click="openAgreement($event, '10')">隐私政策</a>
            </span>
          </label>
          <p v-if="notice" role="status" class="mt-4 text-sm text-success">{{ notice }}</p>
          <div v-if="error" role="alert" class="alert alert-soft alert-error mt-4 text-sm"><span>{{ error }}</span></div>
          <div class="modal-action">
            <button type="button" class="btn" :disabled="working" @click="close">取消</button>
            <button class="btn btn-primary" :disabled="working || !validMobile || !/^\d{4,8}$/.test(code.trim()) || !accepted">
              <span v-if="pending" class="loading loading-spinner loading-xs" aria-hidden="true"></span>
              {{ pending ? '登录中…' : '登录' }}
            </button>
          </div>
        </form>
      </div>
      <form method="dialog" class="modal-backdrop" @submit.prevent="close"><button :disabled="working">关闭</button></form>
    </dialog>
  </Teleport>
</template>
