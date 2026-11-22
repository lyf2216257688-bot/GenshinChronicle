<script setup lang="ts">
import { ref } from 'vue'
import type { Citation, ReportInput } from '../api/client.ts'
const p = defineProps<{ citation: Citation | null; question: string }>()
const emit = defineEmits<{ save: [ReportInput] }>()
const note = ref(''),
  category = ref<ReportInput['category']>(p.citation ? 'citation' : 'answer')
</script>
<template>
  <p class="context-label">{{ question }}</p>
  <p v-if="citation" class="eyebrow">引用 {{ citation.citation_id }}</p>
  <form class="form" @submit.prevent="emit('save', { category, note: note.trim(), citation })">
    <label
      >问题类型<select v-model="category">
        <option value="answer">回答错误</option>
        <option value="missing_evidence">证据遗漏</option>
        <option value="citation">引用问题</option>
        <option value="other">其他问题</option>
      </select></label
    >
    <label
      >说明<textarea
        v-model="note"
        rows="5"
        maxlength="4000"
        placeholder="哪句话或哪处引用需要复查？"
        required
      ></textarea>
    </label>
    <button class="primary" :disabled="!note.trim()">保存反馈</button>
  </form>
</template>
