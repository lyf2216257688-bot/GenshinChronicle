<script setup lang="ts">
import type { Source } from '../api/client.ts'
defineProps<{ source: Source }>()
defineEmits<{ report: [] }>()
</script>
<template>
  <p class="eyebrow">{{ source.citation.citation_id }} · 本轮证据</p>
  <h3>{{ source.title }}</h3>
  <blockquote v-if="source.lines.length" class="source-quote">
    <div v-for="(line, i) in source.lines" :key="i">
      <small v-if="line.dialogue">{{ line.speaker || '来源未标明说话者' }}</small>
      <p>{{ line.text }}</p>
      <p v-for="(c, j) in line.conditions" :key="j"><small>来源条件</small> {{ c }}</p>
      <small v-if="line.condition_status === 'unresolved'">此处的条件或连接尚待解释。</small>
    </div>
  </blockquote>
  <blockquote v-else class="source-quote">
    <p v-for="(t, i) in source.texts" :key="i">{{ t }}</p>
  </blockquote>
  <p class="muted">{{ source.support_note }}</p>
  <details open>
    <summary>父级原文</summary>
    <p v-if="!source.has_parent">尚无已保存的父级原文。</p>
    <p v-for="(t, i) in source.parent_texts" :key="i" class="source-line">{{ t }}</p>
  </details>
  <details v-if="source.lines.length">
    <summary>本次回读的全部原句</summary>
    <p v-for="(t, i) in source.texts" :key="i" class="source-line">{{ t }}</p>
  </details>
  <details v-for="(value, key) in source.context" :key="key">
    <summary>{{ key }}</summary>
    <pre>{{ JSON.stringify(value, null, 2) }}</pre>
  </details>
  <details>
    <summary>Clean 与 Raw 来源定位</summary>
    <pre>{{ JSON.stringify(source.locators, null, 2) }}</pre>
  </details>
  <button class="secondary" @click="$emit('report')">反馈此引用</button>
</template>
