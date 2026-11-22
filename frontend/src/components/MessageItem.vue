<script setup lang="ts">
import { computed } from 'vue'
import type { Citation, Turn } from '../api/client.ts'
import TurnProgress from './TurnProgress.vue'
const props = defineProps<{ turn: Turn }>()
defineEmits<{
  source: [Citation]
  trace: []
  report: []
  correct: [string]
  stop: []
  resume: []
}>()
const parts = computed(() => {
  const bindings = new Map(props.turn.citations.map((c) => ['[' + c.citation_id + ']', c]))
  return (props.turn.answer ?? '')
    .split(/(\[E\d+\])/g)
    .map((text) => ({ text, cite: bindings.get(text) }))
})
</script>
<template>
  <article class="turn" :id="'turn-' + turn.id">
    <div class="user-message">
      <div class="bubble">{{ turn.question || '原问题无法读取' }}</div>
      <button class="text-action" @click="$emit('correct', turn.user_message)">纠正这条</button>
    </div>
    <div class="assistant-heading">
      <span class="small-mark" aria-hidden="true">GC</span><strong>GenshinChronicle</strong>
    </div>
    <div v-if="turn.has_answer" class="answer">
      <template v-for="(p, i) in parts" :key="i">
        <button
          v-if="p.cite"
          class="inline-cite"
          :aria-label="'查看引用 ' + p.cite.citation_id"
          @click="$emit('source', p.cite)"
        >
          {{ p.cite.citation_id }}</button
        ><template v-else>{{ p.text }}</template>
      </template>
    </div>
    <p v-if="turn.detail" class="explanation">{{ turn.detail }}</p>
    <p v-if="turn.has_answer && !turn.citations.length" class="explanation">
      本回答没有已绑定的原文引用。
    </p>
    <div v-if="turn.draft && !turn.has_answer" class="draft-text">
      <small>生成草稿 · 尚未发布</small>
      <p>{{ turn.draft }}</p>
    </div>
    <TurnProgress :turn="turn" @stop="$emit('stop')" @resume="$emit('resume')" />
    <div v-if="turn.assistant_message" class="turn-tools">
      <button v-if="turn.can_trace" @click="$emit('trace')">查证过程</button>
      <button @click="$emit('report')">反馈</button
      ><button @click="$emit('correct', turn.assistant_message)">纠正回答</button>
    </div>
  </article>
</template>
