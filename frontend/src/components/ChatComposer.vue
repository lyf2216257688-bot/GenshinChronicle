<script setup lang="ts">
import { ref, watch, nextTick } from 'vue'
import type { Draft } from '../api/client.ts'
import ChatIcon from './ChatIcon.vue'
const p = defineProps<{ draft: Draft; enabled: boolean; sending: boolean }>()
const emit = defineEmits<{
  edit: [Partial<Pick<Draft, 'text' | 'kind' | 'target'>>]
  send: []
  grants: []
}>()
const input = ref<HTMLTextAreaElement | null>(null)
const composing = ref(false)
watch(
  () => p.draft.target,
  async (v) => {
    if (v) {
      await nextTick()
      input.value?.focus()
    }
  },
)
function key(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing && !composing.value && e.keyCode !== 229) {
    e.preventDefault()
    if (p.enabled && !p.sending && p.draft.text.trim()) emit('send')
  }
}
</script>
<template>
  <div class="composer-wrap">
    <div class="composer">
      <div v-if="draft.target" class="reply-to">
        正在纠正已选消息
        <select
          aria-label="纠正类型"
          :value="draft.kind"
          @change="
            emit('edit', { kind: ($event.target as HTMLSelectElement).value as Draft['kind'] })
          "
        >
          <option value="correction_fact">剧情前提</option>
          <option value="correction_intent">讨论范围</option>
        </select>
        <button class="text-action" @click="emit('edit', { kind: 'question', target: null })">
          取消关联
        </button>
      </div>
      <textarea
        ref="input"
        aria-label="提问"
        placeholder="输入剧情问题，或继续追问…"
        rows="2"
        maxlength="12000"
        :value="draft.text"
        :disabled="sending"
        @input="emit('edit', { text: ($event.target as HTMLTextAreaElement).value })"
        @compositionstart="composing = true"
        @compositionend="composing = false"
        @keydown="key"
      ></textarea>
      <div class="composer-tools">
        <label v-if="!draft.target" class="check"
          ><input
            type="checkbox"
            :checked="draft.kind === 'requirement'"
            @change="
              emit('edit', {
                kind: ($event.target as HTMLInputElement).checked ? 'requirement' : 'question',
              })
            "
          />补充条件</label
        >
        <span class="muted key-hint">Shift + Enter 换行</span>
        <button v-if="!enabled" class="secondary" @click="emit('grants')">
          <ChatIcon name="settings" />开启问答批次
        </button>
        <button
          v-else
          class="primary"
          :disabled="sending || !draft.text.trim()"
          @click="emit('send')"
        >
          <ChatIcon name="send" />
          {{ sending ? '正在保存…' : '发送' }}
        </button>
      </div>
    </div>
  </div>
</template>
