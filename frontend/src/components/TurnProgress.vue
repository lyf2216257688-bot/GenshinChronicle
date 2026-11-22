<script setup lang="ts">
import type { Turn } from '../api/client.ts'
defineProps<{ turn: Turn }>()
defineEmits<{ stop: []; resume: [] }>()
const labels: Record<Turn['state'], string> = {
  queued: '已排队',
  running: '进行中',
  interrupted: '执行已中断',
  completed: '已完成',
  partial: '部分完成',
  cancelled: '已停止',
  unknown: '远端结果未知',
  failed: '执行失败',
  blocked: '记录校验受阻',
}
</script>
<template>
  <div class="progress" role="status">
    <span class="status-dot" aria-hidden="true"></span> <strong>{{ labels[turn.state] }}</strong
    ><span v-if="turn.stage && (turn.watching || turn.can_resume)">{{ turn.stage }}</span>
    <button v-if="turn.can_stop" class="text-action" @click="$emit('stop')">停止本轮</button>
    <button v-if="turn.can_resume" class="text-action" @click="$emit('resume')">继续本轮</button>
  </div>
</template>
