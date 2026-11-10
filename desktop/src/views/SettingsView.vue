<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { storeToRefs } from "pinia";
import { useRoute } from "vue-router";
import type { CloudConfig } from "../api";
import { cloudApi, type ModelConnectionTestResult, type OssConnectionTestResult } from "../services/cloud";
import { useSessionStore } from "../stores/session";
import SecretInput from "../components/SecretInput.vue";
import ProductPlatformLoginCard from "../components/product/ProductPlatformLoginCard.vue";
import { registerExitGuard } from "../exit-guards";
const session = useSessionStore();
const { busy, connected, error } = storeToRefs(session);
const cloudSections = [
  { id: "text", label: "文本剪辑模型" },
  { id: "asr", label: "语音识别模型" },
  { id: "oss", label: "OSS 存储" },
  { id: "douyin", label: "抖音登录" },
  { id: "product-platform", label: "星斗平台" },
] as const;
type CloudSectionId = (typeof cloudSections)[number]["id"];
const activeCloudSection = ref<CloudSectionId>("text");
const route = useRoute();
watch(() => route.query.cloudTab, (value) => {
  const section = cloudSections.find(section => section.id === value);
  if (section) activeCloudSection.value = section.id;
}, { immediate: true });
function onCloudTabKeydown(event: KeyboardEvent, index: number) {
  const nextIndex =
    event.key === "ArrowRight"
      ? (index + 1) % cloudSections.length
      : event.key === "ArrowLeft"
        ? (index - 1 + cloudSections.length) % cloudSections.length
        : event.key === "Home"
          ? 0
          : event.key === "End"
            ? cloudSections.length - 1
            : -1;
  if (nextIndex < 0) return;
  event.preventDefault();
  activeCloudSection.value = cloudSections[nextIndex].id;
  (event.currentTarget as HTMLElement).parentElement
    ?.querySelectorAll<HTMLButtonElement>("[role='tab']")
    [nextIndex]?.focus();
}
const config = ref<CloudConfig>({
  region: "cn",
  base_url: "",
  planner_model: "",
  asr_base_url: "",
  asr_model: "",
  asr_timeout_seconds: 300,
  oss_endpoint: "",
  oss_bucket: "",
  chat_timeout_seconds: 300,
});
const savedConfig = ref<CloudConfig | null>(null);
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
const testingOss = ref(false);
const ossTestResult = ref<OssConnectionTestResult | null>(null);
let ossTestVersion = 0;
const canTestOss = computed(() => connected.value && !busy.value && !testingOss.value &&
  Boolean(config.value.oss_endpoint.trim() && config.value.oss_bucket.trim() &&
    (secretDrafts.value.oss_access_key_id.trim() || secrets.value.oss_access_key_id) &&
    (secretDrafts.value.oss_access_key_secret.trim() || secrets.value.oss_access_key_secret)));
watch([() => config.value.oss_endpoint, () => config.value.oss_bucket,
  () => secretDrafts.value.oss_access_key_id, () => secretDrafts.value.oss_access_key_secret], () => {
    ossTestVersion++;
    ossTestResult.value = null;
  });
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
const hasUnsavedChanges = computed(
  () =>
    Boolean(
      savedConfig.value &&
      JSON.stringify(config.value) !== JSON.stringify(savedConfig.value),
    ) ||
    Boolean(
      modelKey.value.trim() ||
      asrKey.value.trim() ||
      douyinCookie.value.trim() ||
      Object.values(secretDrafts.value).some((value) => value.trim()),
    ),
);
let unregisterExitGuard: (() => void) | undefined;
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
async function testOssConnection() {
  if (!canTestOss.value) return;
  const version = ++ossTestVersion;
  const started = Date.now();
  testingOss.value = true;
  ossTestResult.value = null;
  try {
    const result = await cloudApi.testOss({oss_endpoint: config.value.oss_endpoint, oss_bucket: config.value.oss_bucket},
      secretDrafts.value.oss_access_key_id.trim(), secretDrafts.value.oss_access_key_secret.trim());
    if (!disposed && version === ossTestVersion) ossTestResult.value = result;
  } catch (cause) {
    if (!disposed && version === ossTestVersion) ossTestResult.value = {
      ok: false, elapsed_ms: Date.now() - started,
      message: cause instanceof Error ? cause.message : "测试失败，请重试",
    };
  } finally {
    if (!disposed) testingOss.value = false;
  }
}
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
  savedConfig.value = result.config;
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
  savedConfig.value = {
    ...(savedConfig.value || config.value),
    ...Object.fromEntries(fields.map((name) => [name, saved[name]])),
  } as CloudConfig;
}
async function saveSettings() {
  if (testingOss.value) return;
  await session.act(async () => {
    await saveConfigFields(["oss_endpoint", "oss_bucket"]);
    for (const { name } of ossSecretFields) {
      const value = secretDrafts.value[name].trim();
      if (!value) continue;
      const result = await cloudApi.saveSecret(name, value);
      secretDrafts.value[name] = "";
      secrets.value[name] = true;
      secretPreviews.value[name] = result.preview;
    }
    session.message = "OSS 存储配置已保存";
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
    await saveConfigFields([
      "asr_base_url",
      "asr_model",
      "asr_timeout_seconds",
    ]);
    if (asrKey.value.trim()) {
      const result = await cloudApi.saveSecret(
        "asr_api_key",
        asrKey.value.trim(),
      );
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
      "抖音登录 Cookie 已保存到应用设置；停止并恢复监控后生效，当前场次会先完成收尾";
  });
}

onMounted(() => {
  unregisterExitGuard = registerExitGuard(() => hasUnsavedChanges.value);
  void session.act(loadSettings);
});
onUnmounted(() => {
  unregisterExitGuard?.();
  disposed = true;
  modelKey.value = "";
  asrKey.value = "";
  douyinCookie.value = "";
  secretDrafts.value = { oss_access_key_id: "", oss_access_key_secret: "" };
});
</script>
<template>
  <section class="settings-panel">
    <div class="settings-tabs" role="tablist" aria-label="云端设置">
      <button
        v-for="(section, index) in cloudSections"
        :id="`settings-tab-${section.id}`"
        :key="section.id"
        type="button"
        role="tab"
        :aria-selected="activeCloudSection === section.id"
        :aria-controls="`settings-panel-${section.id}`"
        :tabindex="activeCloudSection === section.id ? 0 : -1"
        :class="{ active: activeCloudSection === section.id }"
        @click="activeCloudSection = section.id"
        @keydown="onCloudTabKeydown($event, index)">
        {{ section.label }}
      </button>
    </div>
    <div class="cloud-settings-panels">
      <section
        id="settings-panel-text"
        v-show="activeCloudSection === 'text'"
        role="tabpanel"
        aria-labelledby="settings-tab-text"
        class="card card-border bg-base-100 settings-card settings-model-card">
        <form @submit.prevent="saveModelConnection">
          <label for="model-base-url">Base URL</label>
          <input
            id="model-base-url"
            v-model="config.base_url"
            type="url"
            required
            placeholder="https://dashscope.aliyuncs.com/compatible-mode/v1" />
          <p class="hint">
            使用兼容 Chat Completions 的接口，不含 /chat/completions。
          </p>
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
          <p class="hint">
            仅用于文本剪辑。输入新密钥可替换，留空保留已保存的密钥。
          </p>
          <label for="planner-model">模型 API ID</label>
          <input
            id="planner-model"
            v-model="config.planner_model"
            required
            maxlength="100" />
          <label for="chat-timeout">请求等待时间 / 秒</label>
          <input
            id="chat-timeout"
            v-model.number="config.chat_timeout_seconds"
            type="number"
            min="30"
            max="900"
            required />
          <p class="hint">
            默认 300 秒。新分析、组片和失败任务重试使用保存后的文本配置。
          </p>
          <p class="hint">
            测试会向当前文本模型发送一条简短请求，可能产生少量费用；不测试语音识别或
            OSS。
          </p>
          <div class="settings-actions settings-model-actions">
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
      <section
        id="settings-panel-asr"
        v-show="activeCloudSection === 'asr'"
        role="tabpanel"
        aria-labelledby="settings-tab-asr"
        class="card card-border bg-base-100 settings-card settings-model-card">
        <form @submit.prevent="saveAsrConnection">
          <label for="asr-base-url">Base URL</label>
          <input
            id="asr-base-url"
            v-model="config.asr_base_url"
            type="url"
            required
            placeholder="https://dashscope.aliyuncs.com" />
          <p class="hint">
            使用 Qwen 原生多模态音频接口的服务地址，不含完整识别接口路径。
          </p>
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
          <p class="hint">
            仅用于语音识别，需单独保存。输入新密钥可替换，留空保留已保存的密钥。
          </p>
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
          <p class="hint">
            默认 300
            秒。新分析和失败任务重试使用保存后的语音配置，进行中的任务保持提交时配置。
          </p>
          <p class="hint">
            每段音频最多 2
            分钟；模型必须支持此接口并返回句级时间戳，只有整段文本时无法精确粗剪。
          </p>
          <div class="settings-actions">
            <button class="btn btn-primary" :disabled="busy || !connected">
              保存语音模型
            </button>
          </div>
        </form>
      </section>
      <section
        id="settings-panel-oss"
        v-show="activeCloudSection === 'oss'"
        role="tabpanel"
        aria-labelledby="settings-tab-oss"
        class="card card-border bg-base-100 settings-card">
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
          <div class="secret-fields">
            <div
              v-for="field in ossSecretFields"
              :key="field.name"
              class="secret-field">
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
              </div>
            </div>
          </div>
          <div class="settings-actions">
            <button type="button" class="btn" :disabled="!canTestOss" @click="testOssConnection">
              {{ testingOss ? "测试中…" : "测试 OSS" }}
            </button>
            <button class="btn btn-primary" :disabled="busy || testingOss || !connected">
              保存 OSS 配置
            </button>
          </div>
          <p class="hint">测试会上传一个很小的测试对象，按 Bucket 生命周期规则清理；不保存表单，不上传用户音视频。最长等待 30 秒。</p>
          <p v-if="testingOss" role="status" class="hint">正在测试 OSS，请稍候…</p>
          <div v-if="ossTestResult" :role="ossTestResult.ok ? 'status' : 'alert'" class="alert alert-soft mt-3 text-sm"
            :class="ossTestResult.ok ? 'alert-success' : 'alert-error'">
            <span>{{ ossTestResult.message }} · {{ (ossTestResult.elapsed_ms / 1000).toFixed(1) }} 秒</span>
          </div>
        </form>
      </section>
      <section
        id="settings-panel-douyin"
        v-show="activeCloudSection === 'douyin'"
        role="tabpanel"
        aria-labelledby="settings-tab-douyin"
        class="card card-border bg-base-100 settings-card">
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
      <section
        v-if="activeCloudSection === 'product-platform'"
        id="settings-panel-product-platform"
        role="tabpanel"
        aria-labelledby="settings-tab-product-platform"
        class="card card-border bg-base-100 settings-card">
        <ProductPlatformLoginCard />
      </section>
    </div>
  </section>
</template>

<style scoped>
.settings-model-actions {
  flex-direction: row;
  align-items: center;
  justify-content: flex-end;
  gap: 0.5rem;
  padding: 1.5rem 0 0;
}
</style>
