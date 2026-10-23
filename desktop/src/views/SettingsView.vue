<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { storeToRefs } from 'pinia'
import type { CloudConfig } from '../api'
import { cloudApi } from '../services/cloud'
import { useSessionStore } from '../stores/session'
import SecretInput from '../components/SecretInput.vue'
const session=useSessionStore()
const {busy,connected,error}=storeToRefs(session)
const config = ref<CloudConfig>({region:'cn',base_url:'https://dashscope.aliyuncs.com/compatible-mode/v1',planner_model:'qwen3.8-flash',asr_model:'qwen-audio-3.1-asr-flash',oss_endpoint:'',oss_bucket:'',chat_timeout_seconds:300})
const secrets = ref<Record<string, boolean>>({})
const secretPreviews = ref<Record<string, string>>({})
type ServiceSecretName = 'oss_access_key_id' | 'oss_access_key_secret'
const serviceSecretFields: {name: ServiceSecretName; label: string; placeholder: string}[] = [
  {name:'oss_access_key_id',label:'OSS AccessKey ID',placeholder:'填写 RAM 用户的 AccessKey ID'},
  {name:'oss_access_key_secret',label:'OSS AccessKey Secret',placeholder:'填写 RAM 用户的 AccessKey Secret'},
]
const ossSecretFields = serviceSecretFields
const secretDrafts = ref<Record<ServiceSecretName,string>>({oss_access_key_id:'',oss_access_key_secret:''})
const modelKey = ref('')
const douyinCookie = ref('')
const modelKeyConfigured = computed(()=>Boolean(secrets.value.llm_api_key))
const modelKeyPreview = computed(()=>modelKeyConfigured.value ? secretPreviews.value.llm_api_key || '' : '')

let disposed=false
async function loadSettings() {
  const result=await cloudApi.load()
  if(disposed) return
  config.value=result.config;secrets.value=result.secrets;secretPreviews.value=result.secret_previews
}
async function saveConfigFields(fields: (keyof CloudConfig)[]) {
  const current=await cloudApi.load()
  const changes=Object.fromEntries(fields.map(name=>[name,config.value[name]]))
  const saved=await cloudApi.save({...current.config,...changes})
  config.value={...config.value,...Object.fromEntries(fields.map(name=>[name,saved[name]]))}
}
async function saveSettings() { await session.act(async()=>{
  await saveConfigFields(['oss_endpoint','oss_bucket']);session.message='OSS 存储配置已保存'
}) }
async function saveSecret(name: ServiceSecretName) { await session.act(async()=>{
  const value=secretDrafts.value[name].trim()
  if(!value) return
  const result=await cloudApi.saveSecret(name,value)
  secretDrafts.value[name]=''
  secrets.value[name]=true
  secretPreviews.value[name]=result.preview
  session.message=`${serviceSecretFields.find(field=>field.name===name)?.label} 已保存`
}) }
async function saveModelConnection() { await session.act(async()=>{
  await saveConfigFields(['base_url','planner_model','asr_model','chat_timeout_seconds'])
  if(modelKey.value.trim()) {
    const value=modelKey.value.trim()
    const result=await cloudApi.saveSecret('llm_api_key',value)
    secretPreviews.value.llm_api_key=result.preview
    modelKey.value=''
    secrets.value.llm_api_key=true
  }
  session.message=`模型连接已保存，语音模型：${config.value.asr_model}；新分析和失败任务重试使用此设置`
}) }
async function saveDouyinCookie() { await session.act(async()=>{
  const value=douyinCookie.value.trim()
  if(!value) return
  const result=await cloudApi.saveSecret('douyin_cookie',value)
  douyinCookie.value='';secrets.value.douyin_cookie=true;secretPreviews.value.douyin_cookie=result.preview
  session.message='抖音登录 Cookie 已保存到应用设置；停止并恢复监控后生效，当前录制分段会先封存'
}) }


onMounted(()=>{void session.act(loadSettings)})
onUnmounted(()=>{disposed=true;modelKey.value='';douyinCookie.value='';secretDrafts.value={oss_access_key_id:'',oss_access_key_secret:''}})
</script>
<template><section class="settings-panel">
        <div class="intro-strip">配置模型连接后即可使用云端分析。文本剪辑决策和语音识别共用百炼模型连接，音频保存在 OSS。</div>
        <div class="settings-grid">
        <section class="panel settings-card settings-model-card">
        <div class="settings-card-heading"><span class="settings-step">01</span><div><h2>模型连接</h2><p>文本剪辑决策与语音模型</p></div></div>
        <form @submit.prevent="saveModelConnection">
          <label for="model-base-url">Base URL</label>
          <input id="model-base-url" v-model="config.base_url" type="url" required placeholder="https://dashscope.aliyuncs.com/compatible-mode/v1"/>
          <p class="hint">语音识别使用同一主机的多模态接口，两个模型共用此密钥。</p>
          <label for="model-api-key">模型 API Key · {{ modelKeyConfigured?'已配置':'未配置' }}</label>
          <SecretInput id="model-api-key" v-model="modelKey" :saved="modelKeyConfigured" secret-name="llm_api_key" :preview="modelKeyPreview" :disabled="busy" placeholder="填写百炼 API Key 或对应网关的 API Key"/>
          <p class="hint">全选输入新密钥可替换，留空保留原值。剪辑决策和语音识别共用此 API Key。</p>
          <label for="planner-model">文本剪辑决策模型 API ID</label><input id="planner-model" v-model="config.planner_model" required/>
          <div class="section-divider"></div>
          <h3>语音识别</h3>
          <label for="asr-model">语音模型 API ID</label>
          <input id="asr-model" v-model="config.asr_model" required maxlength="100" placeholder="qwen-audio-3.1-asr-flash"/>
          <p class="hint">默认 qwen-audio-3.1-asr-flash。保存后，新分析和失败任务重试使用此模型；进行中的任务保持提交时配置。</p>
          <p class="hint">可填写兼容此多模态音频接口的模型 ID。每段最多 2 分钟；粗剪必须有句级时间戳，只有整段文本时会明确报错。</p>
          <label for="chat-timeout">文本剪辑决策等待时间 / 秒</label>
          <input id="chat-timeout" v-model.number="config.chat_timeout_seconds" type="number" min="30" max="900" required/>
          <p class="hint">默认 300 秒。此配置用于新分析和组片任务，2 分钟语音请求等待 300 秒。</p>
          <div class="settings-actions"><button class="primary" :disabled="busy||!connected">保存模型连接</button></div>
        </form>
        <div class="section-divider"></div>
        <h3>抖音直播登录</h3>
        <p class="hint">商品详情接口需要登录态。粘贴浏览器开发者工具中该请求的完整 Cookie 请求头；内容保存到本机应用设置。留空时保留原值。</p>
        <form class="secret-field" @submit.prevent="saveDouyinCookie">
          <label for="douyin-cookie">抖音登录 Cookie <span class="secret-field-status">{{ secrets.douyin_cookie?'已配置':'未配置' }}</span></label>
          <div class="secret-field-controls">
            <SecretInput id="douyin-cookie" v-model="douyinCookie" :saved="Boolean(secrets.douyin_cookie)" secret-name="douyin_cookie" :preview="secretPreviews.douyin_cookie || ''" :disabled="busy" placeholder="粘贴 Cookie 请求头内容"/>
            <button class="primary" :disabled="busy||!connected||!douyinCookie.trim()">保存 Cookie</button>
          </div>
        </form>
        </section>
        <section class="panel settings-card">
        <div class="settings-card-heading"><span class="settings-step">02</span><div><h2>OSS</h2><p>音频存储与访问凭据</p></div></div>
        <form @submit.prevent="saveSettings">
          <p class="hint">语音识别使用私有 Bucket 的音频与临时签名地址，请为音频对象配置生命周期清理。</p>
          <label for="oss-endpoint">OSS Endpoint</label>
          <input id="oss-endpoint" v-model="config.oss_endpoint" placeholder="https://oss-cn-beijing.aliyuncs.com"/>
          <div><label for="oss-bucket">OSS Bucket</label><input id="oss-bucket" v-model="config.oss_bucket" placeholder="私有 Bucket 名称"/></div>
          <div class="settings-actions"><button class="primary" :disabled="busy||!connected">保存 OSS 配置</button></div>
        </form>
        <div class="section-divider"></div>
        <h3>OSS 访问密钥</h3>
        <p class="hint">每项单独保存，留空保留原值。</p>
        <div class="secret-fields">
          <form v-for="field in ossSecretFields" :key="field.name" class="secret-field" @submit.prevent="saveSecret(field.name)">
            <label :for="field.name">{{ field.label }} <span class="secret-field-status">{{ secrets[field.name]?'已配置':'未配置' }}</span></label>
            <div class="secret-field-controls">
              <SecretInput :id="field.name" v-model="secretDrafts[field.name]" :saved="Boolean(secrets[field.name])" :secret-name="field.name" :preview="secretPreviews[field.name] || ''" :disabled="busy" :placeholder="field.placeholder"/>
              <button class="primary" :disabled="busy||!connected||!secretDrafts[field.name].trim()">保存</button>
            </div>
          </form>
        </div>
        </section>
        </div>
      </section></template>
