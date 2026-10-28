import { ref } from 'vue'
import { defineStore } from 'pinia'
import { api } from '../api'
import { jobsApi } from '../services/jobs'
import { errorMessage } from '../error-message'

export const useSessionStore = defineStore('session', () => {
  const connected=ref(false), busy=ref(false), navigating=ref(false), activeJobs=ref(0)
  const error=ref(''), errorRevision=ref(0), message=ref(''), pendingImportId=ref('')
  let connecting: Promise<void> | undefined
  async function connect() {
    if(connected.value) return
    if(!connecting) connecting=api('/auth','POST').then(()=>{connected.value=true}).finally(()=>{connecting=undefined})
    await connecting
  }
  function report(cause: unknown) { error.value=errorMessage(cause);errorRevision.value++ }
  async function act(action: () => Promise<unknown>) {
    if(busy.value) return
    busy.value=true;error.value='';message.value=''
    try { await action() } catch(cause) { report(cause) } finally { busy.value=false }
  }
  async function refreshSummary() { activeJobs.value=(await jobsApi.summary()).active }
  return {connected,busy,navigating,activeJobs,error,errorRevision,message,pendingImportId,connect,report,act,refreshSummary}
})
