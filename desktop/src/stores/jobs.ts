import { ref } from 'vue'
import { defineStore } from 'pinia'
import type { Job } from '../api'
import { errorMessage } from '../error-message'
import { jobsApi, JOB_PAGE_SIZE, type JobCursor } from '../services/jobs'

export const useJobsStore = defineStore('jobs', () => {
  const jobs=ref<Job[]>([]), loading=ref(false), loadingMore=ref(false)
  const hasMore=ref(false), initialized=ref(false), loadError=ref('')
  let refreshing: Promise<void> | undefined, appending: Promise<void> | undefined
  let failedOperation: 'refresh' | 'more' = 'refresh'

  // Refresh visited pages atomically so status updates never collapse the loaded list.
  function load(): Promise<void> {
    if(refreshing) return refreshing
    refreshing=(async()=>{
      if(appending) await appending
      loading.value=true
      try {
        const oldest=jobs.value.at(-1), result: Job[]=[]
        let before: JobCursor | undefined, more=true
        while(more) {
          const page=await jobsApi.page(before), items=page.slice(0,JOB_PAGE_SIZE)
          result.push(...items)
          more=page.length>JOB_PAGE_SIZE;before=items.at(-1)
          if(!oldest || !before || before.created<oldest.created ||
            (before.created===oldest.created && before.id<=oldest.id)) break
        }
        jobs.value=result;hasMore.value=more;initialized.value=true;loadError.value=''
      } catch(cause) {
        failedOperation='refresh'
        loadError.value=errorMessage(cause)
      } finally {loading.value=false}
    })().finally(()=>{refreshing=undefined})
    return refreshing
  }

  function loadMore(): Promise<void> {
    if(appending) return appending
    // A refresh owns the cursor until it finishes; the view rechecks the bottom afterward.
    if(refreshing || !hasMore.value) return Promise.resolve()
    loadingMore.value=true
    const before=jobs.value.at(-1)
    appending=(async()=>{
      try {
        const page=await jobsApi.page(before), ids=new Set(jobs.value.map(job=>job.id))
        jobs.value=[...jobs.value,...page.slice(0,JOB_PAGE_SIZE).filter(job=>!ids.has(job.id))]
        hasMore.value=page.length>JOB_PAGE_SIZE;loadError.value=''
      } catch(cause) {
        failedOperation='more'
        loadError.value=errorMessage(cause)
      } finally {loadingMore.value=false}
    })().finally(()=>{appending=undefined})
    return appending
  }
  function retry() {return failedOperation==='more' ? loadMore() : load()}
  return {jobs,loading,loadingMore,hasMore,initialized,loadError,load,loadMore,retry}
})
