<script setup lang="ts">
import { computed, nextTick, onMounted, onBeforeUnmount, reactive, ref } from 'vue'
import { useChat } from './chat/state.ts'
import type { Chat, Feedback, Mode } from './api/client.ts'
import ChatSidebar from './components/ChatSidebar.vue'
import MessageList from './components/MessageList.vue'
import ChatComposer from './components/ChatComposer.vue'
import EvidencePanel from './components/EvidencePanel.vue'
import FeedbackForm from './components/FeedbackForm.vue'
import BatchPanel from './components/BatchPanel.vue'

const s = reactive(useChat()),
  nav = ref(false),
  title = ref(''),
  panelBusy = ref(false)
const titles = {
  batch: '批次设置',
  source: '引用原文',
  trace: '本轮查证过程',
  report: '记录问题',
  reports: '反馈记录',
  rename: '重命名会话',
}
const categories = {
  answer: '回答错误',
  missing_evidence: '证据遗漏',
  citation: '引用问题',
  other: '其他问题',
}
const liveBatch = computed(() => s.batch?.state === 'open' && s.batch.expires * 1000 > now.value)
const now = ref(Date.now())
let clock: ReturnType<typeof setInterval>
onMounted(() => {
  void s.safe(s.init)
  clock = setInterval(() => {
    now.value = Date.now()
  }, 1000)
})
onBeforeUnmount(() => clearInterval(clock))
function create(mode: Mode) {
  nav.value = false
  void s.safe(() => s.create(mode))
}
async function panelAction(fn: () => Promise<unknown>) {
  if (panelBusy.value) return
  panelBusy.value = true
  try {
    await s.safe(fn)
  } finally {
    panelBusy.value = false
  }
}
async function revisit(f: Feedback) {
  await s.safe(async () => {
    const id = await s.revisit(f)
    await nextTick()
    document.getElementById('turn-' + id)?.scrollIntoView({ block: 'center' })
  })
}
function showBatches() {
  s.closePanel()
  s.panel = 'batch'
  nav.value = false
  void s.safe(s.refreshBatches)
}
function closeDetails() {
  s.closePanel()
  nav.value = false
}
function selectChat(chat: Chat) {
  nav.value = false
  void s.safe(() => s.open(chat))
}
function showReports() {
  nav.value = false
  void s.safe(s.showReports)
}
function editTitle() {
  if (!s.current) return
  title.value = s.current.title
  s.panel = 'rename'
}
</script>
<template>
  <div class="workspace" @keydown.esc="closeDetails">
    <ChatSidebar
      :chats="s.chats"
      :active="s.current?.id"
      :expanded="nav"
      :busy="s.busy || s.sending"
      @create="create"
      @open="selectChat"
      @grants="showBatches"
      @reports="showReports"
      @hide="nav = false"
    />
    <main>
      <header class="topbar">
        <button class="mobile-toggle" aria-label="打开会话导航" @click="nav = true">☰</button>
        <div class="header-title">
          <span class="eyebrow">世界观问答</span>
          <h1>{{ s.current?.title ?? 'GenshinChronicle' }}</h1>
        </div>
        <button v-if="s.current" class="subtle rename" @click="editTitle">
          重命名
        </button>
        <button v-if="s.current" class="mode-label" @click="showBatches">
          {{ s.current.mode === 'MOCK' ? '离线演示' : '真实问答' }} ·
          {{ liveBatch ? '批次可用' : '待开启' }}
        </button>
      </header>
      <div v-if="s.error" class="banner error" role="alert">
        <span>{{ s.error }}</span
        ><button @click="s.safe(s.reload)">重新载入</button
        ><button aria-label="关闭错误提示" @click="s.error = ''">×</button>
      </div>
      <div v-if="s.notice" class="banner" role="status">
        {{ s.notice }}<button aria-label="关闭提示" @click="s.notice = ''">×</button>
      </div>
      <div v-if="s.reconnecting" class="banner" role="status">
        正在重新连接本机服务，已保存的轮次将从上次进度接续显示。
      </div>
      <template v-if="s.current">
        <MessageList
          :turns="s.rows"
          :before="s.before"
          :loading="s.loading"
          @older="s.safe(s.older)"
          @source="(t, c) => s.safe(() => s.inspect('source', t, c))"
          @trace="(t) => s.safe(() => s.inspect('trace', t))"
          @report="(t) => s.report(t)"
          @correct="s.correct"
          @control="(t, a) => s.safe(() => s.control(t, a))"
        />
        <ChatComposer
          v-if="s.draft"
          :draft="s.draft"
          :enabled="liveBatch"
          :sending="s.sending"
          @edit="s.edit"
          @send="s.safe(s.send)"
          @grants="showBatches"
        />
      </template>
      <div v-else class="welcome">
        <span class="large-mark" aria-hidden="true">GC</span>
        <h2>原神世界观问答</h2>
        <p>围绕剧情持续追问，随时回到引用原文。</p>
        <div>
          <button class="primary" :disabled="s.busy" @click="create('LIVE')">开始真实对话</button>
          <button class="secondary" :disabled="s.busy" @click="create('MOCK')">体验离线演示</button>
        </div>
      </div>
    </main>
    <aside v-if="s.panel" class="panel" :aria-label="titles[s.panel]">
      <header>
        <h2>{{ titles[s.panel] }}</h2>
        <button aria-label="关闭详情" class="subtle" @click="s.closePanel">×</button>
      </header>
      <div class="panel-body">
        <p v-if="s.panelLoading" class="muted">正在读取已保存的材料…</p>
        <BatchPanel
          v-if="s.panel === 'batch'"
          :items="s.batches"
          :mode="s.current?.mode ?? 'LIVE'"
          :info="s.info"
          :busy="panelBusy"
          @grant="(v) => panelAction(() => s.grant(v))"
          @close="(v) => panelAction(() => s.endBatch(v))"
        />
        <EvidencePanel
          v-if="s.panel === 'source' && s.source"
          :source="s.source"
          @report="s.selected && s.report(s.selected, s.selectedCitation)"
        />
        <template v-if="s.panel === 'source' && !s.source && !s.panelLoading && s.selected">
          <p>这处引用暂时无法读取。</p>
          <button class="secondary" @click="s.report(s.selected, s.selectedCitation)">
            反馈此引用
          </button></template
        >
        <template v-if="s.panel === 'trace' && s.trace"
          ><h3>检索问题</h3>
          <ul>
            <li v-for="(q, i) in s.trace.queries" :key="i">{{ q }}</li>
          </ul>
          <p>原文读取 {{ s.trace.reads }} 次</p>
          <h3>候选资料取舍</h3>
          <dl>
            <template v-for="(n, k) in s.trace.dispositions" :key="k"
              ><dt>{{ k }}</dt>
              <dd>{{ n }}</dd></template
            >
          </dl>
          <p>停止原因 · {{ s.trace.stop_reason }}</p></template
        >
        <FeedbackForm
          v-if="s.panel === 'report' && s.selected"
          :key="s.selected.id + (s.selectedCitation?.citation_id ?? '')"
          :question="s.selected.question"
          :citation="s.selectedCitation"
          @save="(data) => panelAction(() => s.saveReport(data))"
        />
        <template v-if="s.panel === 'reports'"
          ><p v-if="!s.reports.length" class="muted">还没有反馈记录。</p>
          <article class="feedback-card" v-for="f in s.reports" :key="f.id">
            <small
              >{{ categories[f.category] }} ·
              {{ new Date(f.created * 1000).toLocaleDateString() }}</small
            >
            <h3>{{ f.question }}</h3>
            <p>{{ f.note }}</p>
            <small>{{ f.engine }}</small
            ><button class="text-action" @click="revisit(f)">
              回到原回答{{ f.citation ? '及引用' : '' }}
            </button>
          </article></template
        >
        <form
          class="form"
          v-if="s.panel === 'rename'"
          @submit.prevent="panelAction(() => s.rename(title.trim()))"
        >
          <label>会话名称<input v-model="title" maxlength="100" required /></label
          ><button class="primary" :disabled="!title.trim()">保存名称</button>
        </form>
      </div>
    </aside>
  </div>
</template>
