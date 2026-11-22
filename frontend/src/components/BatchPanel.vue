<script setup lang="ts">
import { computed, ref } from 'vue'
import type { Batch, Grant, Mode, Runtime } from '../api/client.ts'
const p = defineProps<{ items: Batch[]; mode: Mode; info: Runtime | null; busy: boolean }>()
defineEmits<{ grant: [Grant]; close: [Batch] }>()
const mode = ref(p.mode),
  requests = ref(8),
  minutes = ref(60),
  consent = ref(false)
const b = computed(() => p.items.find((item) => item.mode === mode.value))
const states = {
  open: '可用',
  closed: '已关闭',
  expired: '已过期',
  exhausted: '额度已用尽',
  read_only: '原配置已变化，只读',
}
</script>
<template>
  <label class="field"
    >问答模式<select v-model="mode">
      <option value="LIVE">真实问答</option>
      <option value="MOCK">离线演示</option>
    </select></label
  >
  <div v-if="b" class="batch-facts">
    <h3>{{ b.model }} · {{ states[b.state] }}</h3>
    <p>
      请求 {{ b.requests }} / {{ b.max_requests }} · 已知用量
      {{ b.known_tokens.toLocaleString() }} Token
    </p>
    <p>
      批次 Token 上限 {{ b.max_tokens.toLocaleString()
      }}<span v-if="b.unknown_usage"> · 用量未知 {{ b.unknown_usage }} 次</span>
    </p>
    <p>到期 {{ new Date(b.expires * 1000).toLocaleString() }}</p>
    <p v-if="b.state === 'open'">关闭将停止本批未结束的轮次。</p>
    <button v-if="b.state === 'open'" class="secondary" :disabled="busy" @click="$emit('close', b)">
      关闭此批次
    </button>
  </div>
  <form
    v-if="b?.state !== 'open'"
    class="form"
    @submit.prevent="
      $emit('grant', { mode, requests, minutes, confirm_live: mode === 'LIVE' && consent })
    "
  >
    <p>
      {{
        mode === 'LIVE'
          ? '使用官方 ' + info?.live_model + '。同批次内的真实会话共享额度，一轮可能需要多次请求。'
          : '使用本机合成语料和模拟回答。'
      }}
    </p>
    <label
      >最多请求数<input v-model.number="requests" type="number" min="2" max="10" required
    /></label>
    <label
      >有效时长（分钟）<input v-model.number="minutes" type="number" min="1" max="120" required
    /></label>
    <template v-if="mode === 'LIVE'"
      ><p v-if="info" class="muted">
        每批保守 Token 上限
        {{ (info.tokens_per_request * requests).toLocaleString() }}。实际费用以供应商账单为准。
      </p>
      <p v-if="!info?.key_configured" class="explanation">启动环境中缺少 DEEPSEEK_API_KEY。</p>
      <p v-if="info?.issues.length" class="explanation">{{ info.issues.join('、') }}未就绪。</p>
      <label class="check"
        ><input
          type="checkbox"
          v-model="consent"
          required
        />我授权本批次在上述范围内产生模型费用。</label
      ></template
    >
    <button
      class="primary"
      :disabled="
        busy || (mode === 'LIVE' && (!consent || !info?.key_configured || !!info?.issues.length))
      "
    >
      开启批次
    </button>
  </form>
</template>
