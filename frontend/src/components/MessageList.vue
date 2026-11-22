<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'
import type { Citation, Turn } from '../api/client.ts'
import MessageItem from './MessageItem.vue'
const props = defineProps<{ turns: Turn[]; before: number | null; loading: boolean }>()
defineEmits<{
  older: []
  source: [Turn, Citation]
  trace: [Turn]
  report: [Turn]
  correct: [string]
  control: [Turn, 'stop' | 'resume']
}>()
const scroll = ref<HTMLElement | null>(null)
let follow = true
function onScroll() {
  const el = scroll.value
  if (el) follow = el.scrollHeight - el.scrollTop - el.clientHeight < 90
}
watch(
  () => [props.turns.at(-1)?.id, props.turns.at(-1)?.draft, props.turns.at(-1)?.has_answer],
  async () => {
    if (follow) {
      await nextTick()
      if (scroll.value) scroll.value.scrollTop = scroll.value.scrollHeight
    }
  },
)
</script>
<template>
  <div class="transcript" ref="scroll" @scroll="onScroll">
    <div class="reading">
      <button v-if="before" class="older" @click="$emit('older')">加载更早的消息</button>
      <p v-if="loading" class="muted">正在读取会话…</p>
      <div v-else-if="!turns.length" class="empty">
        <span class="large-mark" aria-hidden="true">GC</span>
        <h2>开始一段世界观讨论</h2>
        <p>提出一个剧情问题，再沿着回答里的引用追问。</p>
      </div>
      <MessageItem
        v-for="t in turns"
        :key="t.id"
        :turn="t"
        @source="(c) => $emit('source', t, c)"
        @trace="$emit('trace', t)"
        @report="$emit('report', t)"
        @correct="(mid) => $emit('correct', mid)"
        @stop="$emit('control', t, 'stop')"
        @resume="$emit('control', t, 'resume')"
      />
    </div>
  </div>
</template>
