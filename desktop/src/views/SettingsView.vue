<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { storeToRefs } from "pinia";
import type { CloudConfig } from "../api";
import { cloudApi, type ModelConnectionTestResult } from "../services/cloud";
import { useSessionStore } from "../stores/session";
import SecretInput from "../components/SecretInput.vue";
const session = useSessionStore();
const { busy, connected, error } = storeToRefs(session);
const config = ref<CloudConfig>({
  region: "cn",
  base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1",
  planner_model: "qwen3.8-flash",
  asr_base_url: "https://dashscope.aliyuncs.com",
  asr_model: "qwen-audio-3.1-asr-flash",
  asr_timeout_seconds: 300,
  oss_endpoint: "",
  oss_bucket: "",
  chat_timeout_seconds: 300,
});
const secrets = ref<Record<string, boolean>>({});
const secretPreviews = ref<Record<string, string>>({});
type ServiceSecretName = "oss_access_key_id" | "oss_access_key_secret";
const serviceSecretFields: {
  name: ServiceSecretName;
  label: string;
  placeholder: string;
}[] = [
  {
    name: "oss_access_key_id",
    label: "OSS AccessKey ID",
    placeholder: "填写 RAM 用户的 AccessKey ID",
  },
  {
    name: "oss_access_key_secret",
    label: "OSS AccessKey Secret",
    placeholder: "填写 RAM 用户的 AccessKey Secret",
  },
];
const ossSecretFields = serviceSecretFields;
const secretDrafts = ref<Record<ServiceSecretName, string>>({
  oss_access_key_id: "",
  oss_access_key_secret: "",
});
const modelKey = ref("");
const asrKey = ref("");
const asrKeyConfigured = computed(() => Boolean(secrets.value.asr_api_key));
const asrKeyPreview = computed(() =>
  asrKeyConfigured.value ? secretPreviews.value.asr_api_key || "" : "",
);
const douyinCookie = ref("");
const modelKeyConfigured = computed(() => Boolean(secrets.value.llm_api_key));
const modelKeyPreview = computed(() =>
  modelKeyConfigured.value ? secretPreviews.value.llm_api_key || "" : "",
);
const testingModel = ref(false);
const modelTestResult = ref<ModelConnectionTestResult | null>(null);
const canTestModel = computed(
  () =>
    connected.value &&
    !busy.value &&
    !testingModel.value &&
    Boolean(
      config.value.base_url.trim() &&
      config.value.planner_model.trim() &&
      (modelKey.value.trim() || modelKeyConfigured.value),
    ),
);
let modelTestVersion = 0;
watch(
  [
    () => config.value.base_url,
    () => config.value.planner_model,
    () => config.value.chat_timeout_seconds,
    () => modelKey.value,
  ],
  () => {
    modelTestVersion++;
    modelTestResult.value = null;
  },
);

let disposed = false;
async function testModelConnection() {
  if (!canTestModel.value) return;
  const version = ++modelTestVersion;
  const current = {
    base_url: config.value.base_url,
    planner_model: config.value.planner_model,
    chat_timeout_seconds: config.value.chat_timeout_seconds,
  };
  const started = Date.now();
  testingModel.value = true;
  modelTestResult.value = null;
  try {
    const result = await cloudApi.testModel(current, modelKey.value.trim());
    if (!disposed && version === modelTestVersion)
      modelTestResult.value = result;
  } catch (cause) {
    if (!disposed && version === modelTestVersion)
      modelTestResult.value = {
        ok: false,
        model: current.planner_model,
        elapsed_ms: Date.now() - started,
        message: cause instanceof Error ? cause.message : "测试失败，请重试",
      };
  } finally {
    if (!disposed) testingModel.value = false;
  }
}
async function loadSettings() {
  const result = await cloudApi.load();
  if (disposed) return;
  config.value = result.config;
  secrets.value = result.secrets;
  secretPreviews.value = result.secret_previews;
}
async function saveConfigFields(fields: (keyof CloudConfig)[]) {
  const current = await cloudApi.load();
  const changes = Object.fromEntries(
    fields.map((name) => [name, config.value[name]]),
  );
  const saved = await cloudApi.save({ ...current.config, ...changes });
  config.value = {
    ...config.value,
    ...Object.fromEntries(fields.map((name) => [name, saved[name]])),
  };
}
async function saveSettings() {
  await session.act(async () => {
    await saveConfigFields(["oss_endpoint", "oss_bucket"]);
    session.message = "OSS 存储配置已保存";
  });
}
async function saveSecret(name: ServiceSecretName) {
  await session.act(async () => {
    const value = secretDrafts.value[name].trim();
    if (!value) return;
    const result = await cloudApi.saveSecret(name, value);
    secretDrafts.value[name] = "";
    secrets.value[name] = true;
    secretPreviews.value[name] = result.preview;
    session.message = `${serviceSecretFields.find((field) => field.name === name)?.label} 已保存`;
  });
}
async function saveModelConnection() {
  await session.act(async () => {
    await saveConfigFields([
      "base_url",
      "planner_model",
      "chat_timeout_seconds",
    ]);
    if (modelKey.value.trim()) {
      const value = modelKey.value.trim();
      const result = await cloudApi.saveSecret("llm_api_key", value);
      secretPreviews.value.llm_api_key = result.preview;
      modelKey.value = "";
      secrets.value.llm_api_key = true;
    }
    session.message = "文本剪辑模型已保存；新分析和失败任务重试使用此设置";
  });
}
async function saveAsrConnection() {
  await session.act(async () => {
    await saveConfigFields(["asr_base_url", "asr_model", "asr_timeout_seconds"]);
    if (asrKey.value.trim()) {
      const result = await cloudApi.saveSecret("asr_api_key", asrKey.value.trim());
      secretPreviews.value.asr_api_key = result.preview;
      asrKey.value = "";
      secrets.value.asr_api_key = true;
    }
    session.message = "语音识别模型已保存；新分析和失败任务重试使用此设置";
  });
}
async function saveDouyinCookie() {
  await session.act(async () => {
    const value = douyinCookie.value.trim();
    if (!value) return;
    const result = await cloudApi.saveSecret("douyin_cookie", value);
    douyinCookie.value = "";
    secrets.value.douyin_cookie = true;
    secretPreviews.value.douyin_cookie = result.preview;
    session.message =
      "抖音登录 Cookie 已保存到应用设置；停止并恢复监控后生效，当前录制分段会先封存";
  });
}

onMounted(() => {
  void session.act(loadSettings);
});
onUnmounted(() => {
  disposed = true;
  modelKey.value = "";
  asrKey.value = "";
  douyinCookie.value = "";
  secretDrafts.value = { oss_access_key_id: "", oss_access_key_secret: "" };
});
</script>
<template>
  <section class="settings-panel">
    <div class="intro-strip">
      文本剪辑与语音识别分别配置连接，独立保存。语音识别使用 OSS 存储音频；直播登录按需配置。
    </div>
    <div class="settings-grid">
      <section class="card card-border bg-base-100 settings-card settings-model-card">
        <div class="settings-card-heading">
          <div>
            <h2>文本剪辑模型</h2>
            <p>根据识别文本选择剪辑区间、生成组片方案</p>
          </div>
        </div>
        <form @submit.prevent="saveModelConnection">
          <label for="model-base-url">Base URL</label>
          <input
            id="model-base-url"
            v-model="config.base_url"
            type="url"
            required
            placeholder="https://dashscope.aliyuncs.com/compatible-mode/v1" />
          <p class="hint">使用兼容 Chat Completions 的接口，不含 /chat/completions。</p>
          <label for="model-api-key">
            API Key · {{ modelKeyConfigured ? "已配置" : "未配置" }}
          </label>
          <SecretInput
            id="model-api-key"
            v-model="modelKey"
            :saved="modelKeyConfigured"
            secret-name="llm_api_key"
            :preview="modelKeyPreview"
            :disabled="busy"
            placeholder="填写文本模型服务的 API Key" />
          <p class="hint">仅用于文本剪辑。输入新密钥可替换，留空保留已保存的密钥。</p>
          <label for="planner-model">模型 API ID</label>
          <input id="planner-model" v-model="config.planner_model" required maxlength="100" />
          <label for="chat-timeout">请求等待时间 / 秒</label>
          <input
            id="chat-timeout"
            v-model.number="config.chat_timeout_seconds"
            type="number"
            min="30"
            max="900"
            required />
          <p class="hint">默认 300 秒。新分析、组片和失败任务重试使用保存后的文本配置。</p>
          <p class="hint">测试会向当前文本模型发送一条简短请求，可能产生少量费用；不测试语音识别或 OSS。</p>
          <div class="settings-actions flex-wrap items-center gap-2">
            <button
              type="button"
              class="btn"
              :disabled="!canTestModel"
              @click="testModelConnection">
              <span
                v-if="testingModel"
                class="loading loading-spinner loading-xs"
                aria-hidden="true"></span>
              {{ testingModel ? "测试中…" : "测试模型" }}
            </button>
            <button
              class="btn btn-primary"
              :disabled="busy || testingModel || !connected">
              保存文本模型
            </button>
          </div>
          <p v-if="testingModel" role="status" class="hint">
            正在测试文本模型，请稍候…
          </p>
          <div
            v-if="modelTestResult"
            :role="modelTestResult.ok ? 'status' : 'alert'"
            class="alert alert-soft mt-3 text-sm"
            :class="modelTestResult.ok ? 'alert-success' : 'alert-error'">
            <span>
              {{ modelTestResult.message }} · {{ modelTestResult.model }} ·
              {{ (modelTestResult.elapsed_ms / 1000).toFixed(2) }} 秒
            </span>
          </div>
        </form>
      </section>
      <section class="card card-border bg-base-100 settings-card settings-model-card">
        <div class="settings-card-heading">
          <div>
            <h2>语音识别模型</h2>
            <p>将源视频音频识别为文本及源时间戳</p>
          </div>
        </div>
        <form @submit.prevent="saveAsrConnection">
          <label for="asr-base-url">Base URL</label>
          <input
            id="asr-base-url"
            v-model="config.asr_base_url"
            type="url"
            required
            placeholder="https://dashscope.aliyuncs.com" />
          <p class="hint">使用 Qwen 原生多模态音频接口的服务地址，不含完整识别接口路径。</p>
          <label for="asr-api-key">
            API Key · {{ asrKeyConfigured ? "已配置" : "未配置" }}
          </label>
          <SecretInput
            id="asr-api-key"
            v-model="asrKey"
            :saved="asrKeyConfigured"
            secret-name="asr_api_key"
            :preview="asrKeyPreview"
            :disabled="busy"
            placeholder="填写语音识别服务的 API Key" />
          <p class="hint">仅用于语音识别，需单独保存。输入新密钥可替换，留空保留已保存的密钥。</p>
          <label for="asr-model">模型 API ID</label>
          <input
            id="asr-model"
            v-model="config.asr_model"
            required
            maxlength="100"
            placeholder="qwen-audio-3.1-asr-flash" />
          <label for="asr-timeout">请求等待时间 / 秒</label>
          <input
            id="asr-timeout"
            v-model.number="config.asr_timeout_seconds"
            type="number"
            min="30"
            max="900"
            required />
          <p class="hint">默认 300 秒。新分析和失败任务重试使用保存后的语音配置，进行中的任务保持提交时配置。</p>
          <p class="hint">每段音频最多 2 分钟；模型必须支持此接口并返回句级时间戳，只有整段文本时无法精确粗剪。</p>
          <div class="settings-actions">
            <button class="btn btn-primary" :disabled="busy || !connected">保存语音模型</button>
          </div>
        </form>
      </section>
      <section class="card card-border bg-base-100 settings-card">
        <div class="settings-card-heading">
          <div>
            <h2>OSS 存储</h2>
            <p>音频存储与访问凭据</p>
          </div>
        </div>
        <form @submit.prevent="saveSettings">
          <p class="hint">
            语音识别使用私有 Bucket
            的音频与临时签名地址，请为音频对象配置生命周期清理。
          </p>
          <label for="oss-endpoint">OSS Endpoint</label>
          <input
            id="oss-endpoint"
            v-model="config.oss_endpoint"
            placeholder="https://oss-cn-beijing.aliyuncs.com" />
          <div>
            <label for="oss-bucket">OSS Bucket</label>
            <input
              id="oss-bucket"
              v-model="config.oss_bucket"
              placeholder="私有 Bucket 名称" />
          </div>
          <div class="settings-actions">
            <button class="btn btn-primary" :disabled="busy || !connected">
              保存 OSS 配置
            </button>
          </div>
        </form>
        <div class="section-divider"></div>
        <h3>OSS 访问密钥</h3>
        <p class="hint">每项单独保存，留空保留原值。</p>
        <div class="secret-fields">
          <form
            v-for="field in ossSecretFields"
            :key="field.name"
            class="secret-field"
            @submit.prevent="saveSecret(field.name)">
            <label :for="field.name">
              {{ field.label }}
              <span class="secret-field-status">
                {{ secrets[field.name] ? "已配置" : "未配置" }}
              </span>
            </label>
            <div class="secret-field-controls">
              <SecretInput
                :id="field.name"
                v-model="secretDrafts[field.name]"
                :saved="Boolean(secrets[field.name])"
                :secret-name="field.name"
                :preview="secretPreviews[field.name] || ''"
                :disabled="busy"
                :placeholder="field.placeholder" />
              <button
                class="btn btn-primary"
                :disabled="
                  busy || !connected || !secretDrafts[field.name].trim()
                ">
                保存
              </button>
            </div>
          </form>
        </div>
      </section>
      <section class="card card-border bg-base-100 settings-card">
        <div class="settings-card-heading">
          <div>
            <h2>抖音直播登录</h2>
            <p>直播商品详情的登录凭据</p>
          </div>
        </div>
        <p class="hint">
          商品详情接口需要登录态。粘贴浏览器开发者工具中该请求的完整 Cookie
          请求头；内容保存到本机应用设置。留空时保留原值。
        </p>
        <form class="secret-field" @submit.prevent="saveDouyinCookie">
          <label for="douyin-cookie">
            抖音登录 Cookie
            <span class="secret-field-status">
              {{ secrets.douyin_cookie ? "已配置" : "未配置" }}
            </span>
          </label>
          <div class="secret-field-controls">
            <SecretInput
              id="douyin-cookie"
              v-model="douyinCookie"
              :saved="Boolean(secrets.douyin_cookie)"
              secret-name="douyin_cookie"
              :preview="secretPreviews.douyin_cookie || ''"
              :disabled="busy"
              placeholder="粘贴 Cookie 请求头内容" />
            <button
              class="btn btn-primary"
              :disabled="busy || !connected || !douyinCookie.trim()">
              保存 Cookie
            </button>
          </div>
        </form>
      </section>
    </div>
  </section>
</template>
