<script setup lang="ts">
import type { Source } from '../api/client.ts'
import ChatIcon from './ChatIcon.vue'
defineProps<{ source: Source; demo: boolean }>()
defineEmits<{ report: [] }>()

const scopes: Record<Source['read_scope'], string> = {
  chunk: '检索片段',
  parent: '结构化父级',
  natural_parent: '自然父级',
  graph: '对话结构',
}
const parentStages = {
  in_packet: '这份父级原文已列入本轮回答的证据包。',
  saved_read: '本轮查证期间已保存这份父级原文，最终引用以本轮证据包为准。',
  after_answer: '这份父级原文在回答生成后补读，供核查上下文。',
}
</script>
<template>
  <div class="source-meta">
    <span class="citation-label">{{ source.citation.citation_id }}</span>
    <span>{{ scopes[source.read_scope] }}<span v-if="demo"> · 合成资料</span></span>
  </div>
  <h3 class="source-title">{{ source.title }}</h3>
  <div class="source-excerpt">
    <p class="source-excerpt-label">引用原句</p>
    <p v-if="source.location_gap" class="source-note">历史回执只保留了部分正文的定位，此处展示可核对的原句。</p>
    <blockquote class="source-quote">
      <template v-if="source.lines.length">
        <div v-for="(line, i) in source.lines" :key="i">
          <small v-if="line.dialogue">{{ line.speaker || '来源未标明说话者' }}</small>
          <p>{{ line.text }}</p>
          <small v-if="source.read_scope === 'parent'">定位 {{ line.locator }}</small>
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
  <details>
    <summary>父级原文<ChatIcon name="right" :size="15" /></summary>
    <p v-if="source.parent_status === 'integrity_error'">补充父级的记录校验失败。上方引用仍可核对。</p>
    <p v-else-if="source.parent_status === 'read_failed'">本轮未能补读父级原文。上方引用仍可核对。</p>
    <p v-else-if="!source.has_parent">尚无已保存的父级原文。</p>
    <p v-if="source.parent_stage">{{ parentStages[source.parent_stage] }}</p>
    <template v-if="source.read_scope !== 'natural_parent'">
      <p v-for="(t, i) in source.parent_texts" :key="i" class="source-line"><small>{{ i + 1 }}.</small> {{ t }}</p>
      <details v-if="source.parent_locators.length">
        <summary>父级 Clean 与 Raw 来源定位<ChatIcon name="right" :size="15" /></summary>
        <pre>{{ JSON.stringify(source.parent_locators.map((item, i) => ({ 序号: i + 1, ...item })), null, 2) }}</pre>
      </details>
    </template>
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
    <pre>{{ JSON.stringify(source.locators.map((item, i) => ({ 序号: i + 1, ...item })), null, 2) }}</pre>
  </details>
  <button class="secondary" @click="$emit('report')"><ChatIcon name="flag" />反馈此引用</button>
</template>
