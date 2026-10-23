import { ref } from 'vue'
import { defineStore } from 'pinia'
import { jobsApi } from '../services/jobs'

export const useJobsStore = defineStore('jobs', () => {
  const jobs=ref<Awaited<ReturnType<typeof jobsApi.list>>>([])
  let request=0
  async function load() {
    const current=++request, result=await jobsApi.list()
    if(current===request) jobs.value=result
  }
  return {jobs,load}
})
