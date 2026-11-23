import { computed, ref, shallowRef, onBeforeUnmount } from 'vue'
import { ApiError, client as defaultApi } from '../api/client.ts'
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

export type Panel = 'batch' | 'source' | 'trace' | 'report' | 'reports' | 'rename' | 'delete' | null
type Stream = {
  disconnect?: () => void
  retry?: ReturnType<typeof setTimeout>
  failures: number
  offline: boolean
}

export function useChat(api: Client = defaultApi) {
  const actions = createActions(api)
  const chats = ref<Chat[]>([]),
    selection = ref<Chat | null>(null),
    rows = ref<Turn[]>([])
  const current = computed(() => chats.value.find((c) => c.id === selection.value?.id) ?? selection.value)
  const draft = ref<Draft | null>(null),
    info = ref<Runtime | null>(null),
    batches = ref<Batch[]>([])
  const loading = ref(false),
    sending = ref(false),
    busy = ref(false),
    error = ref(''),
    runtimeError = ref(''),
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
    navigation = 0,
    panelGeneration = 0,
    listGeneration = 0
  let timer: ReturnType<typeof setTimeout> | undefined
  const streams = new Map<string, Stream>()
  const rejectedStreams = new Set<string>()
  function fail(e: unknown) {
    error.value = e instanceof Error ? e.message : '无法连接本机服务。'
  }
  async function safe(fn: () => Promise<unknown>) {
    try {
      error.value = ''
      await fn()
      return true
    } catch (e) {
      fail(e)
      return false
    }
  }
  function inView(sid: string, epoch: number) {
    return epoch === generation && current.value?.id === sid
  }
  async function guarded<T>(fn: () => Promise<T>, active: () => boolean): Promise<T | undefined> {
    try {
      const result = await fn()
      if (active()) return result
    } catch (e) {
      if (active()) throw e
    }
  }
  function updateConnection() {
    reconnecting.value = [...streams.values()].some((s) => s.offline)
  }
  function closeStream(tid: string) {
    const stream = streams.get(tid)
    if (!stream) return
    streams.delete(tid)
    stream.disconnect?.()
    clearTimeout(stream.retry)
    updateConnection()
  }
  function closeStreams() {
    for (const tid of streams.keys()) closeStream(tid)
    rejectedStreams.clear()
  }
  function setPanel(kind: Panel) {
    ++panelGeneration
    panel.value = kind
    panelLoading.value = false
  }
  function closePanel() {
    setPanel(null)
  }
  function closeForNavigation(keepIndependentPanel = false) {
    const independent = panel.value === 'batch' || panel.value === 'reports' || panel.value === 'delete'
    if (!keepIndependentPanel || !independent) closePanel()
  }
  function upsert(t: Turn) {
    if (current.value?.id !== t.chat_id) return
    const prior = rows.value.find((row) => row.id === t.id)
    if (prior && (prior.cursor > t.cursor ||
        (t.state !== 'blocked' && prior.result_hash && prior.result_hash !== t.result_hash))) return
    rows.value = [...rows.value.filter((x) => x.id !== t.id), t].sort(
      (a, b) => a.sequence - b.sequence,
    )
    if (!t.watching) closeStream(t.id)
    rows.value.filter((x) => x.watching).forEach(watchTurn)
  }
  function watchTurn(t: Turn) {
    if (streams.has(t.id) || rejectedStreams.has(t.id) || streams.size >= 2) return
    const epoch = generation
    const stream: Stream = { failures: 0, offline: false }
    const active = () => inView(t.chat_id, epoch) && streams.get(t.id) === stream
    streams.set(t.id, stream)

    function recover(offline: boolean) {
      if (!active() || !stream.disconnect) return
      stream.disconnect()
      stream.disconnect = undefined
      stream.offline = offline
      updateConnection()
      void reconcile()
    }

    function connect() {
      const cursor = rows.value.find((row) => row.id === t.id)?.cursor ?? t.cursor
      const disconnect = api.events(
        t.id,
        cursor,
        (e) => {
          if (!active() || stream.disconnect !== disconnect) return
          if (e.kind === 'settled' || e.kind === 'error') {
            recover(e.kind === 'error')
          } else {
            stream.failures = 0
            const index = rows.value.findIndex((r) => r.id === t.id)
            if (index >= 0) rows.value[index] = applyEvent(rows.value[index], e)
          }
        },
        (online) => {
          if (!active() || stream.disconnect !== disconnect) return
          if (online) {
            stream.offline = false
            updateConnection()
          } else recover(true)
        },
      )
      stream.disconnect = disconnect
    }

    function retry(fn: () => void) {
      const delay = Math.min(6000, 300 * 2 ** stream.failures)
      stream.failures = Math.min(5, stream.failures + 1)
      stream.retry = setTimeout(fn, delay)
    }

    async function reconcile() {
      try {
        const tView = await api.turn(t.id)
        if (!active()) return
        upsert(tView)
        if (!active()) {
          void refreshBatches().catch((e) => {
            if (inView(t.chat_id, epoch)) fail(e)
          })
          return
        }
        retry(connect)
      } catch (e) {
        if (!active()) return
        if (e instanceof ApiError && e.status && e.status >= 400 && e.status < 500) {
          rejectedStreams.add(t.id)
          closeStream(t.id)
          fail(e)
          rows.value.filter((row) => row.watching).forEach(watchTurn)
          return
        }
        stream.offline = true
        updateConnection()
        retry(() => void reconcile())
      }
    }

    connect()
  }
  async function refreshBatches() {
    batches.value = (await api.batches()).items
  }
  async function refreshBatchPanel() {
    await Promise.all([refreshRuntime(), refreshBatches()])
  }
  async function refreshRuntime() {
    try {
      info.value = await api.runtime()
      runtimeError.value = ''
    } catch (e) {
      info.value = null
      runtimeError.value = e instanceof Error ? e.message : '无法连接本机聊天服务。'
      throw e
    }
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
  function conflictKey(sid: string) {
    return 'gc:draft-conflict:' + sid
  }
  function edit(patch: Partial<Pick<Draft, 'text' | 'target' | 'kind'>>) {
    if (!editor || sending.value) return
    const local = editor
    local.edit(patch)
    draft.value = local.snapshot()
    sessionStorage.setItem(backupKey(local.sid), JSON.stringify(draft.value))
    clearTimeout(timer)
    timer = setTimeout(() => void saveEditedDraft(local), 220)
  }
  async function saveEditedDraft(local: DraftEditor) {
    try {
      await local.flush()
      if (local === editor) {
        draft.value = local.snapshot()
        if (!local.dirty) sessionStorage.removeItem(backupKey(local.sid))
      }
    } catch (e) {
      if (local === editor) fail(e)
    }
  }
  function clearChat(keepIndependentPanel = false) {
    ++generation
    ++navigation
    clearTimeout(timer)
    closeStreams()
    closeForNavigation(keepIndependentPanel)
    editor = null
    selection.value = null
    draft.value = null
    rows.value = []
    before.value = null
    loading.value = false
    localStorage.removeItem('gc:last-chat')
    history.replaceState(null, '', location.pathname + location.hash)
  }
  async function open(chat: Chat, options: { skipFlush?: boolean; keepIndependentPanel?: boolean } = {}) {
    if (sending.value) return
    const intent = ++navigation
    const prior = editor
    const latest = () => intent === navigation
    if (prior && !options.skipFlush) await guarded(() => prior.flush(), latest)
    if (!latest() || sending.value) return
    clearTimeout(timer)
    closeStreams()
    closeForNavigation(options.keepIndependentPanel)
    const epoch = ++generation
    editor = null
    selection.value = chat
    rows.value = []
    draft.value = null
    before.value = null
    loading.value = true
    localStorage.setItem('gc:last-chat', chat.id)
    history.replaceState(null, '', '?chat=' + encodeURIComponent(chat.id))
    try {
      const model = await guarded(() => actions.open(chat), () => inView(chat.id, epoch) && latest())
      if (!model) return
      editor = model.editor
      draft.value = editor.snapshot()
      const backup = sessionStorage.getItem(backupKey(chat.id))
      if (backup) {
        const old = JSON.parse(backup) as Draft
        if (old.token === draft.value.token && old.revision === draft.value.revision) {
          edit({ text: old.text, kind: old.kind, target: old.target })
        } else if (old.text !== draft.value.text || old.kind !== draft.value.kind || old.target !== draft.value.target) {
          sessionStorage.setItem(conflictKey(chat.id), backup)
          fail(new ApiError('draft_conflict', '服务端草稿版本已变化。此标签页保留了先前的本机暂存草稿，请核对当前内容。'))
        } else {
          sessionStorage.removeItem(backupKey(chat.id))
        }
      }
      before.value = model.page.before
      rows.value = model.page.turns
      rows.value.filter((t) => t.watching).forEach(watchTurn)
      return epoch
    } finally {
      if (epoch === generation) loading.value = false
    }
  }
  async function init(skipFlush = false) {
    const intent = ++navigation
    const [list, runtime, grants] = await Promise.allSettled([
      refreshChats(),
      refreshRuntime(),
      api.batches(),
    ])
    if (intent !== navigation) return
    if (list.status === 'rejected') throw list.reason
    if (runtime.status === 'rejected') fail(runtime.reason)
    if (grants.status === 'fulfilled') batches.value = grants.value.items
    else {
      batches.value = []
      fail(grants.reason)
    }
    const id =
      new URLSearchParams(location.search).get('chat') || localStorage.getItem('gc:last-chat')
    const chat = chats.value.find((c) => c.id === id) ?? chats.value[0]
    if (id && !chats.value.some((c) => c.id === id)) {
      sessionStorage.removeItem(backupKey(id))
      sessionStorage.removeItem(conflictKey(id))
      clearChat(true)
    }
    if (chat) await open(chat, { skipFlush, keepIndependentPanel: true })
    else if (!id) clearChat(true)
  }
  async function refreshChats() {
    const listId = ++listGeneration
    const baseline = new Map(chats.value.map((chat) => [chat.id, chat]))
    const list = await api.chats()
    if (listId !== listGeneration) return
    const changed = new Map(chats.value.filter((chat) => baseline.get(chat.id) !== chat)
      .map((chat) => [chat.id, chat]))
    chats.value = list.items.map((chat) => changed.get(chat.id) ?? chat)
  }
  async function create(mode: Mode) {
    if (busy.value || sending.value) return
    busy.value = true
    try {
      const c = await actions.create(mode)
      await refreshChats()
      await refreshBatches()
      await open(c)
    } finally {
      busy.value = false
    }
  }
  async function send() {
    if (!editor || busy.value || sending.value || !editor.fields.text.trim()) return
    const local = editor, epoch = generation
    sending.value = true
    clearTimeout(timer)
    try {
      const t = await local.send()
      sessionStorage.removeItem(backupKey(local.sid))
      if (!inView(local.sid, epoch)) return
      upsert(t)
      editor = null
      draft.value = null
      const next = await api.draft(local.sid)
      if (!inView(local.sid, epoch)) return
      editor = new DraftEditor(api, local.sid, next)
      draft.value = editor.snapshot()
      await refreshChats()
    } finally {
      sending.value = false
    }
  }
  async function older() {
    if (!current.value || !before.value) return
    const epoch = generation, sid = current.value.id, cursor = before.value
    const page = await guarded(() => api.history(sid, cursor), () => inView(sid, epoch) && before.value === cursor)
    if (page) {
      page.turns.forEach(upsert)
      before.value = page.before
    }
  }
  async function control(t: Turn, action: 'stop' | 'resume') {
    const epoch = generation
    if (!inView(t.chat_id, epoch)) return
    const result = await guarded(() => actions.control(t, action), () => inView(t.chat_id, epoch))
    if (result) upsert(result)
  }
  function correct(mid: string) {
    edit({ target: mid, kind: 'correction_fact' })
    closePanel()
  }
  async function inspect(kind: 'source' | 'trace', t: Turn, citation?: Citation) {
    if (current.value?.id !== t.chat_id) return
    setPanel(kind)
    const epoch = panelGeneration
    const active = () => epoch === panelGeneration
    selected.value = t
    source.value = null
    trace.value = null
    selectedCitation.value = citation ?? null
    panelLoading.value = true
    try {
      if (kind === 'source' && citation) {
        const v = await guarded(() => actions.cite(t, citation), active)
        if (v) source.value = v
      }
      if (kind === 'trace') {
        const v = await guarded(() => api.trace(t.id), active)
        if (v) trace.value = v
      }
    } finally {
      if (epoch === panelGeneration) panelLoading.value = false
    }
  }
  function report(t: Turn, c: Citation | null = null) {
    if (current.value?.id !== t.chat_id) return
    setPanel('report')
    selected.value = t
    selectedCitation.value = c
  }
  async function saveReport(data: ReportInput) {
    if (!selected.value) return
    const target = selected.value, epoch = panelGeneration
    const saved = await guarded(() => actions.report(target, data), () => epoch === panelGeneration)
    if (saved) {
      closePanel()
      notice.value = '反馈已保存。'
    }
  }
  async function showReports() {
    setPanel('reports')
    const epoch = panelGeneration
    const result = await guarded(() => api.reports(), () => epoch === panelGeneration)
    if (result) reports.value = result.items
  }
  async function revisit(f: Feedback) {
    const c = chats.value.find((c) => c.id === f.chat_id)
    if (!c) throw Error('找不到原会话。')
    const epoch = await open(c)
    if (epoch === undefined || !inView(c.id, epoch)) return
    const detailsEpoch = panelGeneration, intent = navigation
    const active = () => inView(c.id, epoch) && detailsEpoch === panelGeneration && intent === navigation
    const t = await guarded(() => api.turn(f.turn_id), active)
    if (!t) return
    if (t.result_hash !== f.result_hash) throw Error('反馈对应的原回答版本无法核验。')
    upsert(t)
    if (f.citation) await inspect('source', t, f.citation)
    if (inView(c.id, epoch)) return t.id
  }
  async function grant(data: Grant) {
    const epoch = panelGeneration
    await api.openBatch(data)
    await refreshBatches()
    if (epoch === panelGeneration) closePanel()
    notice.value = '问答批次已开启。'
  }
  async function endBatch(b: Batch) {
    await api.closeBatch(b.id)
    await refreshBatches()
  }
  async function rename(title: string) {
    const target = current.value
    if (!target) return
    const epoch = panelGeneration
    try {
      const renamed = await api.rename(target.id, title)
      chats.value = chats.value.map((c) => c.id === renamed.id ? renamed : c)
      if (epoch === panelGeneration) closePanel()
    } catch (e) {
      if (epoch === panelGeneration) throw e
    }
  }
  async function removeChat(chat: Chat) {
    if (busy.value || sending.value) return
    busy.value = true
    const epoch = panelGeneration
    try {
      const list = await api.deleteChat(chat.id)
      ++listGeneration
      chats.value = list.items
      sessionStorage.removeItem(backupKey(chat.id))
      sessionStorage.removeItem(conflictKey(chat.id))
      if (current.value?.id === chat.id) {
        clearChat()
        if (chats.value[0]) await open(chats.value[0])
      } else if (epoch === panelGeneration) {
        closePanel()
      }
      notice.value = '会话已删除。'
    } finally {
      busy.value = false
    }
  }
  async function reload() {
    if (!sending.value) await init(true)
  }
  onBeforeUnmount(() => {
    ++generation
    ++navigation
    ++panelGeneration
    ++listGeneration
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
    runtimeError,
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
    removeChat,
    reload,
    setPanel,
    closePanel,
    refreshBatches,
    refreshBatchPanel,
    saveLiveSettings,
    clearLiveSettings,
  }
}
