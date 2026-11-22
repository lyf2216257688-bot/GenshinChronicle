import { computed, ref, shallowRef, onBeforeUnmount } from 'vue'
import { client as defaultApi } from '../api/client.ts'
import type {
  Batch,
  Chat,
  Citation,
  Client,
  Draft,
  Feedback,
  Grant,
  LiveSettings,
  Mode,
  ReportInput,
  Runtime,
  Source,
  Trace,
  Turn,
} from '../api/client.ts'
import { applyEvent, createActions, DraftEditor } from './actions.ts'

export type Panel = 'batch' | 'source' | 'trace' | 'report' | 'reports' | 'rename' | null
export function useChat(api: Client = defaultApi) {
  const actions = createActions(api)
  const chats = ref<Chat[]>([]),
    current = ref<Chat | null>(null),
    rows = ref<Turn[]>([])
  const draft = ref<Draft | null>(null),
    info = ref<Runtime | null>(null),
    batches = ref<Batch[]>([])
  const loading = ref(false),
    sending = ref(false),
    busy = ref(false),
    error = ref(''),
    notice = ref('')
  const before = ref<number | null>(null),
    reconnecting = ref(false),
    panel = ref<Panel>(null)
  const selected = ref<Turn | null>(null),
    source = shallowRef<Source | null>(null),
    trace = ref<Trace | null>(null)
  const selectedCitation = ref<Citation | null>(null),
    reports = ref<Feedback[]>([]),
    panelLoading = ref(false)
  const batch = computed(() => batches.value.find((b) => b.mode === current.value?.mode))
  let editor: DraftEditor | null = null,
    generation = 0,
    panelGeneration = 0
  let timer: ReturnType<typeof setTimeout> | undefined
  const streams = new Map<string, () => void>()
  function fail(e: unknown) {
    error.value = e instanceof Error ? e.message : '无法连接本机服务。'
  }
  async function safe(fn: () => Promise<unknown>) {
    try {
      error.value = ''
      await fn()
    } catch (e) {
      fail(e)
    }
  }
  function closeStreams() {
    streams.forEach((stop) => stop())
    streams.clear()
    reconnecting.value = false
  }
  function closePanel() {
    ++panelGeneration
    panel.value = null
    panelLoading.value = false
  }
  function upsert(t: Turn) {
    if (current.value?.id !== t.chat_id) return
    rows.value = [...rows.value.filter((x) => x.id !== t.id), t].sort(
      (a, b) => a.sequence - b.sequence,
    )
    if (t.watching) watchTurn(t)
  }
  function watchTurn(t: Turn) {
    if (streams.has(t.id) || streams.size >= 2) return
    const epoch = generation
    const stop = api.events(
      t.id,
      t.cursor,
      (e) => {
        if (epoch !== generation) return
        if (e.kind === 'settled' || e.kind === 'error') {
          stop()
          streams.delete(t.id)
          if (e.kind === 'error') {
            error.value = e.text
            return
          }
          void safe(async () => {
            const final = await api.turn(t.id)
            if (epoch !== generation) return
            upsert(final)
            rows.value.filter((x) => x.watching).forEach(watchTurn)
            await refreshBatches()
          })
        } else {
          const index = rows.value.findIndex((r) => r.id === t.id)
          if (index >= 0) rows.value[index] = applyEvent(rows.value[index], e)
        }
      },
      (online) => {
        if (epoch === generation) reconnecting.value = !online
      },
    )
    streams.set(t.id, stop)
  }
  async function refreshBatches() {
    batches.value = (await api.batches()).items
  }
  async function refreshBatchPanel() {
    const [runtime] = await Promise.all([api.runtime(), refreshBatches()])
    info.value = runtime
  }
  async function saveLiveSettings(data: LiveSettings) {
    info.value = await api.saveLiveSettings(data)
    notice.value = 'API 配置已保存在本机。'
    await refreshBatches()
  }
  async function clearLiveSettings() {
    info.value = await api.clearLiveSettings()
    notice.value = '已移除本机保存的 API Key。'
    await refreshBatches()
  }
  function backupKey(sid: string) {
    return 'gc:unsaved:' + sid
  }
  function edit(patch: Partial<Pick<Draft, 'text' | 'target' | 'kind'>>) {
    if (!editor || sending.value) return
    const local = editor
    local.edit(patch)
    draft.value = local.snapshot()
    sessionStorage.setItem(backupKey(local.sid), JSON.stringify(draft.value))
    clearTimeout(timer)
    timer = setTimeout(
      () =>
        void safe(async () => {
          await local.flush()
          if (local === editor) {
            draft.value = local.snapshot()
            sessionStorage.removeItem(backupKey(local.sid))
          }
        }),
      220,
    )
  }
  async function open(chat: Chat, discard = false) {
    if (sending.value) return
    if (editor && !discard) await editor.flush()
    clearTimeout(timer)
    closeStreams()
    closePanel()
    const epoch = ++generation
    editor = null
    current.value = chat
    rows.value = []
    draft.value = null
    loading.value = true
    localStorage.setItem('gc:last-chat', chat.id)
    history.replaceState(null, '', '?chat=' + encodeURIComponent(chat.id))
    try {
      const model = await actions.open(chat)
      if (generation !== epoch) return
      editor = model.editor
      draft.value = editor.snapshot()
      const backup = sessionStorage.getItem(backupKey(chat.id))
      if (backup && !discard) {
        const old = JSON.parse(backup) as Draft
        if (old.token === draft.value.token && old.revision === draft.value.revision)
          edit({ text: old.text, kind: old.kind, target: old.target })
      }
      before.value = model.page.before
      rows.value = model.page.turns
      rows.value.filter((t) => t.watching).forEach(watchTurn)
    } finally {
      if (epoch === generation) loading.value = false
    }
  }
  async function init() {
    const [list, runtime, grants] = await Promise.allSettled([
      api.chats(),
      api.runtime(),
      api.batches(),
    ])
    if (list.status === 'rejected') throw list.reason
    chats.value = list.value.items
    if (runtime.status === 'fulfilled') info.value = runtime.value
    else fail(runtime.reason)
    if (grants.status === 'fulfilled') batches.value = grants.value.items
    else {
      batches.value = []
      fail(grants.reason)
    }
    const id =
      new URLSearchParams(location.search).get('chat') || localStorage.getItem('gc:last-chat')
    const chat = chats.value.find((c) => c.id === id) ?? chats.value[0]
    if (chat) await open(chat)
  }
  async function create(mode: Mode) {
    if (busy.value || sending.value) return
    busy.value = true
    try {
      const c = await actions.create(mode)
      chats.value = (await api.chats()).items
      await refreshBatches()
      await open(c)
    } finally {
      busy.value = false
    }
  }
  async function send() {
    if (!editor || sending.value || !editor.fields.text.trim()) return
    const local = editor
    sending.value = true
    clearTimeout(timer)
    try {
      const t = await local.send()
      upsert(t)
      sessionStorage.removeItem(backupKey(local.sid))
      editor = new DraftEditor(api, local.sid, await api.draft(local.sid))
      draft.value = editor.snapshot()
      chats.value = (await api.chats()).items
      current.value = chats.value.find((c) => c.id === local.sid) ?? current.value
    } finally {
      sending.value = false
    }
  }
  async function older() {
    if (!current.value || !before.value) return
    const epoch = generation,
      page = await api.history(current.value.id, before.value)
    if (epoch === generation) {
      page.turns.forEach(upsert)
      before.value = page.before
    }
  }
  async function control(t: Turn, action: 'stop' | 'resume') {
    upsert(await actions.control(t, action))
  }
  function correct(mid: string) {
    edit({ target: mid, kind: 'correction_fact' })
    closePanel()
  }
  async function inspect(kind: 'source' | 'trace', t: Turn, citation?: Citation) {
    const epoch = ++panelGeneration
    panel.value = kind
    selected.value = t
    source.value = null
    trace.value = null
    selectedCitation.value = citation ?? null
    panelLoading.value = true
    try {
      if (kind === 'source' && citation) {
        const v = await actions.cite(t, citation)
        if (epoch === panelGeneration) source.value = v
      }
      if (kind === 'trace') {
        const v = await api.trace(t.id)
        if (epoch === panelGeneration) trace.value = v
      }
    } finally {
      if (epoch === panelGeneration) panelLoading.value = false
    }
  }
  function report(t: Turn, c: Citation | null = null) {
    ++panelGeneration
    selected.value = t
    selectedCitation.value = c
    panel.value = 'report'
  }
  async function saveReport(data: ReportInput) {
    if (!selected.value) return
    await actions.report(selected.value, data)
    closePanel()
    notice.value = '反馈已保存。'
  }
  async function showReports() {
    closePanel()
    panel.value = 'reports'
    reports.value = (await api.reports()).items
  }
  async function revisit(f: Feedback) {
    const c = chats.value.find((c) => c.id === f.chat_id)
    if (!c) throw Error('找不到原会话。')
    await open(c)
    const t = await api.turn(f.turn_id)
    if (t.result_hash !== f.result_hash) throw Error('反馈对应的原回答版本无法核验。')
    upsert(t)
    if (f.citation) await inspect('source', t, f.citation)
    return t.id
  }
  async function grant(data: Grant) {
    await api.openBatch(data)
    await refreshBatches()
    closePanel()
    notice.value = '问答批次已开启。'
  }
  async function endBatch(b: Batch) {
    await api.closeBatch(b.id)
    await refreshBatches()
  }
  async function rename(title: string) {
    if (!current.value) return
    current.value = await api.rename(current.value.id, title)
    chats.value = (await api.chats()).items
    closePanel()
  }
  async function reload() {
    if (!current.value || !info.value) {
      await init()
      return
    }
    const c = current.value
    sessionStorage.removeItem(backupKey(c.id))
    await open(c, true)
    await refreshBatches()
  }
  onBeforeUnmount(() => {
    ++generation
    clearTimeout(timer)
    closeStreams()
  })
  return {
    chats,
    current,
    rows,
    draft,
    info,
    batches,
    batch,
    loading,
    busy,
    sending,
    error,
    notice,
    before,
    reconnecting,
    panel,
    selected,
    source,
    trace,
    selectedCitation,
    reports,
    panelLoading,
    safe,
    open,
    init,
    create,
    edit,
    send,
    older,
    control,
    correct,
    inspect,
    report,
    saveReport,
    showReports,
    revisit,
    grant,
    endBatch,
    rename,
    reload,
    closePanel,
    refreshBatches,
    refreshBatchPanel,
    saveLiveSettings,
    clearLiveSettings,
  }
}
