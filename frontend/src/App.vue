<script setup lang="ts">
import { computed, nextTick, onMounted, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { useChat } from './chat/state.ts'
import type { Chat, Feedback, Mode } from './api/client.ts'
import ChatSidebar from './components/ChatSidebar.vue'
import MessageList from './components/MessageList.vue'
import ChatComposer from './components/ChatComposer.vue'
import EvidencePanel from './components/EvidencePanel.vue'
import FeedbackForm from './components/FeedbackForm.vue'
import BatchPanel from './components/BatchPanel.vue'
import ChatIcon from './components/ChatIcon.vue'

const s = reactive(useChat()),
  nav = ref(false),
  title = ref(''),
  panelBusy = ref(false)
const navToggle = ref<HTMLButtonElement | null>(null)
const panelClose = ref<HTMLButtonElement | null>(null)
let panelTrigger: HTMLElement | null = null
const titles = {
  batch: '批次设置',
  source: '引用原文',
  trace: '本轮查证过程',
  report: '记录问题',
  reports: '反馈记录',
  rename: '重命名会话',
}
const panelIcons = {
  batch: 'settings',
  source: 'book',
  trace: 'trace',
  report: 'flag',
  reports: 'record',
  rename: 'edit',
} as const
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
watch(() => s.panel, async (panel, previous) => {
  if (panel && !previous) {
    panelTrigger = document.activeElement instanceof HTMLElement ? document.activeElement : null
  }
  await nextTick()
  if (s.panel !== panel) return
  if (panel) {
    panelClose.value?.focus({ preventScroll: true })
  } else {
    const target = panelTrigger?.getClientRects().length ? panelTrigger : navToggle.value
    if (target?.getClientRects().length) target.focus({ preventScroll: true })
    panelTrigger = null
  }
})
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
  void s.safe(s.refreshBatchPanel)
}
function closeDetails() {
  if (s.panel) s.closePanel()
  if (nav.value) hideNav()
}
function hideNav() {
  nav.value = false
  navToggle.value?.focus()
}
async function openNav() {
  if (s.panel) s.closePanel()
  nav.value = true
  await nextTick()
  document.querySelector<HTMLButtonElement>('#chat-navigation .nav-close')?.focus()
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
      @hide="hideNav"
    />
    <main>
      <header class="topbar">
        <button
          ref="navToggle"
          class="icon-button mobile-toggle"
          aria-label="打开会话导航"
          :aria-expanded="nav"
          aria-controls="chat-navigation"
          @click="openNav"
        >
          <ChatIcon name="menu" :size="18" />
        </button>
        <div class="header-title">
          <span v-if="s.current" class="eyebrow">世界观问答</span>
          <h1>{{ s.current?.title ?? '首页' }}</h1>
        </div>
        <div v-if="s.current" class="header-actions">
          <button class="icon-button rename" aria-label="重命名" title="重命名" @click="editTitle">
            <ChatIcon name="edit" />
          </button>
          <button class="mode-label" :class="{ idle: !liveBatch }" @click="showBatches">
            <span class="status-dot" aria-hidden="true"></span>
            <span>
              {{ s.current.mode === 'MOCK' ? '离线演示' : '真实问答' }} ·
              {{ liveBatch ? '批次可用' : '待开启' }}
            </span>
            <ChatIcon name="down" :size="12" />
          </button>
        </div>
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
          :demo="s.current.mode === 'MOCK'"
          :source-turn="s.panel === 'source' ? s.selected?.id : undefined"
          :source-citation="s.panel === 'source' ? s.selectedCitation?.citation_id : undefined"
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
        <section class="welcome-card" aria-labelledby="welcome-title">
          <div class="welcome-content">
            <span class="large-mark"><ChatIcon name="mark" :size="42" /></span>
            <h2 id="welcome-title">原神世界观问答</h2>
            <p>围绕剧情持续追问，随时回到引用原文。</p>
            <div class="welcome-actions">
              <button class="primary" :disabled="s.busy" @click="create('LIVE')">
                <ChatIcon name="plus" />开始真实对话
              </button>
              <button class="secondary" :disabled="s.busy" @click="create('MOCK')">
                <ChatIcon name="flask" />体验离线演示
              </button>
            </div>
          </div>
        </section>
      </div>
    </main>
    <button
      v-if="nav || s.panel"
      class="scrim"
      :class="{ 'panel-scrim': s.panel }"
      aria-label="关闭浮层"
      tabindex="-1"
      @click="closeDetails"
    ></button>
    <aside v-if="s.panel" id="chat-details" class="panel" :aria-label="titles[s.panel]">
      <header>
        <ChatIcon :name="panelIcons[s.panel]" :size="19" />
        <h2>{{ titles[s.panel] }}</h2>
        <button ref="panelClose" aria-label="关闭详情" class="icon-button" @click="s.closePanel">
          <ChatIcon name="close" />
        </button>
      </header>
      <div class="panel-body">
        <p v-if="s.panelLoading" class="muted">正在读取已保存的材料…</p>
        <BatchPanel
          v-if="s.panel === 'batch'"
          :items="s.batches"
          :mode="s.current?.mode ?? 'LIVE'"
          :info="s.info"
          :busy="panelBusy"
          @save-settings="(v) => panelAction(() => s.saveLiveSettings(v))"
          @clear-settings="panelAction(s.clearLiveSettings)"
          @grant="(v) => panelAction(() => s.grant(v))"
          @close="(v) => panelAction(() => s.endBatch(v))"
        />
        <EvidencePanel
          v-if="s.panel === 'source' && s.source"
          :source="s.source"
          :demo="s.current?.mode === 'MOCK'"
          @report="s.selected && s.report(s.selected, s.selectedCitation)"
        />
        <template v-if="s.panel === 'source' && !s.source && !s.panelLoading && s.selected">
          <p class="explanation">这处引用暂时无法读取。</p>
          <button class="secondary" @click="s.report(s.selected, s.selectedCitation)">
            <ChatIcon name="flag" />反馈此引用
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
