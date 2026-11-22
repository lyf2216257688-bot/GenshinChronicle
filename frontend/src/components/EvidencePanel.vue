<script setup lang="ts">
import type { Source } from '../api/client.ts'
import ChatIcon from './ChatIcon.vue'
defineProps<{ source: Source; demo: boolean }>()
defineEmits<{ report: [] }>()
</script>
<template>
  <div class="source-meta">
    <span class="citation-label">{{ source.citation.citation_id }}</span>
    <span>本轮证据<span v-if="demo"> · 合成资料</span></span>
  </div>
  <h3 class="source-title">{{ source.title }}</h3>
  <div class="source-excerpt">
    <p class="source-excerpt-label">引用原句</p>
    <blockquote class="source-quote">
      <template v-if="source.lines.length">
        <div v-for="(line, i) in source.lines" :key="i">
          <small v-if="line.dialogue">{{ line.speaker || '来源未标明说话者' }}</small>
          <p>{{ line.text }}</p>
          <p v-for="(c, j) in line.conditions" :key="j"><small>来源条件</small> {{ c }}</p>
          <small v-if="line.condition_status === 'unresolved'">此处的条件或连接尚待解释。</small>
        </div>
      </template>
      <template v-else>
        <p v-for="(t, i) in source.texts" :key="i">{{ t }}</p>
      </template>
    </blockquote>
  </div>
  <p class="source-note">{{ source.support_note }}</p>
  <details open>
    <summary>父级原文<ChatIcon name="right" :size="15" /></summary>
    <p v-if="!source.has_parent">尚无已保存的父级原文。</p>
    <p v-for="(t, i) in source.parent_texts" :key="i" class="source-line">{{ t }}</p>
  </details>
  <details v-if="source.lines.length">
    <summary>本次回读的全部原句<ChatIcon name="right" :size="15" /></summary>
    <p v-for="(t, i) in source.texts" :key="i" class="source-line">{{ t }}</p>
  </details>
  <details v-for="(value, key) in source.context" :key="key">
    <summary>{{ key }}<ChatIcon name="right" :size="15" /></summary>
    <pre>{{ JSON.stringify(value, null, 2) }}</pre>
  </details>
  <details>
    <summary>Clean 与 Raw 来源定位<ChatIcon name="right" :size="15" /></summary>
    <pre>{{ JSON.stringify(source.locators, null, 2) }}</pre>
  </details>
  <button class="secondary" @click="$emit('report')"><ChatIcon name="flag" />反馈此引用</button>
</template>
