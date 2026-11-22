<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { Batch, Grant, LiveSettings, Mode, Runtime } from '../api/client.ts'
import ChatIcon from './ChatIcon.vue'
const p = defineProps<{ items: Batch[]; mode: Mode; info: Runtime | null; busy: boolean }>()
const emit = defineEmits<{
  grant: [Grant]
  close: [Batch]
  saveSettings: [LiveSettings]
  clearSettings: []
}>()
const mode = ref(p.mode),
  requests = ref(8),
  minutes = ref(60),
  consent = ref(false)
const b = computed(() => p.items.find((item) => item.mode === mode.value))
const endpoint = ref('')
const apiKey = ref('')
watch(() => p.info?.endpoint, (value) => { endpoint.value = value ?? '' }, { immediate: true })
watch(mode, () => { apiKey.value = '' })
const keyStatus = {
  local: '已保存在本机',
  environment: '已从启动环境读取',
  missing: '待配置',
  unavailable: '本机 Key 无法读取',
}
const settingsLocked = computed(() => p.busy || (mode.value === 'LIVE' && b.value?.state === 'open'))
function saveSettings() {
  const key = apiKey.value.trim()
  if (!key || settingsLocked.value) return
  apiKey.value = ''
  consent.value = false
  emit('saveSettings', { endpoint: endpoint.value.trim(), api_key: key })
}
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
  <section v-if="info" class="product-data" aria-label="应用数据目录">
    <h3>应用数据目录</h3>
    <code>{{ info.data_root }}</code>
  </section>
  <details v-if="mode === 'LIVE' && info" class="live-connection" :open="!info.key_configured">
    <summary>
      <span>真实问答配置</span>
      <span class="connection-status" :class="{ pending: !info.key_configured }">
        {{ keyStatus[info.key_source] }}
      </span>
      <ChatIcon name="right" :size="15" />
    </summary>
    <form v-if="info.credential_storage_supported" class="form connection-form" @submit.prevent="saveSettings">
      <label>
        请求地址
        <input v-model="endpoint" type="url" maxlength="512" spellcheck="false" :disabled="settingsLocked" required />
      </label>
      <label>
        API Key
        <input
          v-model="apiKey"
          type="password"
          autocomplete="off"
          spellcheck="false"
          maxlength="512"
          :placeholder="info.key_configured ? '输入新的 API Key 以替换' : '粘贴 DeepSeek API Key'"
          :disabled="settingsLocked"
          required
        />
      </label>
      <p>API Key 由当前 Windows 账号加密，保存在应用数据目录。保存配置后可开启问答批次。</p>
      <p v-if="b?.state === 'open'" class="explanation">关闭当前真实问答批次后可修改配置。</p>
      <div class="connection-actions">
        <button class="secondary" :disabled="settingsLocked || !endpoint.trim() || !apiKey.trim()">
          保存配置
        </button>
        <button
          v-if="info.key_source === 'local' || info.key_source === 'unavailable'"
          type="button"
          class="text-action"
          :disabled="settingsLocked"
          @click="emit('clearSettings')"
        >
          移除本机 Key
        </button>
      </div>
    </form>
    <p v-else>当前运行环境无法使用 Windows 账号加密。可从启动环境读取已有 Key。</p>
  </details>
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
    <h3 class="batch-authorization">批次授权</h3>
    <p>
      {{
        mode === 'LIVE'
          ? '使用官方 ' + info?.live_model + '。同批次内的真实会话共享额度，一轮可能需要多次请求。'
          : '使用本机合成语料和模拟回答。'
      }}
    </p>
    <div class="field-pair">
      <label>
        最多请求数<input v-model.number="requests" type="number" min="2" max="10" required />
      </label>
      <label>
        有效时长（分钟）<input v-model.number="minutes" type="number" min="1" max="120" required />
      </label>
    </div>
    <template v-if="mode === 'LIVE'"
      ><p v-if="info" class="muted">
        每批保守 Token 上限
        {{ (info.tokens_per_request * requests).toLocaleString() }}。实际费用以供应商账单为准。
      </p>
      <p v-if="!info?.key_configured" class="explanation">请先完成上方的真实问答配置。</p>
      <p v-if="info?.issues.length" class="explanation">{{ info.issues.join('、') }}未就绪。</p>
      <label v-if="info?.key_configured" class="check"
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
