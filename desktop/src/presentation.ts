export const stateLabels: Record<string,string> = {queued:'排队中',running:'处理中',pause_requested:'等待暂停',paused:'已暂停',cancel_requested:'正在取消',cancelled:'已取消',succeeded:'已完成',failed:'失败'}
export const kindLabels: Record<string,string> = {import:'素材导入',analyze:'语音分析与粗剪',preview:'拼接预览',export:'成片导出',jianying:'剪映草稿生成',live_product_clips:'商品片段切分'}
export const projectStatusLabels: Record<string,string> = {pending:'待剪辑',processing:'处理中',paused:'已暂停',failed:'失败',completed:'已完成',cancelled:'已取消'}
export const updatedTime = (time: number) => new Date(time*1000).toLocaleString('zh-CN', {month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'})
