<script setup lang="ts">
import { computed } from 'vue'
import type { Citation, Turn } from '../api/client.ts'
import ChatIcon from './ChatIcon.vue'
import TurnProgress from './TurnProgress.vue'
const props = defineProps<{ turn: Turn; demo: boolean; sourceCitation?: string }>()
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
    <div class="assistant-message">
      <span class="small-mark"><ChatIcon name="mark" :size="25" /></span>
      <div class="assistant-content">
        <div class="assistant-heading">
          <strong>GenshinChronicle</strong>
          <small v-if="demo" class="muted">离线演示</small>
        </div>
        <div v-if="turn.has_answer" class="answer">
          <template v-for="(p, i) in parts" :key="i">
            <button
              v-if="p.cite"
              class="inline-cite"
              :aria-label="'查看引用 ' + p.cite.citation_id"
              :aria-expanded="sourceCitation === p.cite.citation_id"
              :aria-controls="sourceCitation === p.cite.citation_id ? 'chat-details' : undefined"
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
        <TurnProgress :turn="turn" @stop="$emit('stop')" @resume="$emit('resume')" />
        <div v-if="turn.draft && !turn.has_answer" class="draft-text">
          <small>生成草稿 · 尚未发布</small>
          <p>{{ turn.draft }}</p>
        </div>
        <div v-if="turn.assistant_message" class="turn-tools">
          <button v-if="turn.can_trace" @click="$emit('trace')">
            <ChatIcon name="trace" :size="14" />查证过程
          </button>
          <button @click="$emit('report')"><ChatIcon name="flag" :size="14" />反馈</button>
          <button @click="$emit('correct', turn.assistant_message)">
            <ChatIcon name="edit" :size="14" />纠正回答
          </button>
        </div>
      </div>
    </div>
  </article>
</template>
