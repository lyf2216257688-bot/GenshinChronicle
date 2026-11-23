import assert from 'node:assert/strict'
import test from 'node:test'
import { createRenderer } from 'vue'
import { ApiError, client } from '../src/api/client.ts'
import type { Chat, Client, Draft, Turn, TurnEvent } from '../src/api/client.ts'
import { useChat } from '../src/chat/state.ts'

function deferred<T>() {
  let resolve!: (result: T) => void
  const promise = new Promise<T>((r) => { resolve = r })
  return { promise, resolve }
}

const [a, b, c]: Chat[] = ['A', 'B', 'C'].map((id) => ({ id, title: id, mode: 'MOCK' }))
const draftFor = (id: string): Draft => ({
  token: id, revision: 0, kind: 'question', target: null, text: '',
})
const turnFor = (id: string): Turn => ({
  id: 'turn-' + id, chat_id: id, sequence: 1, cursor: 0, watching: false,
}) as Turn

function mount(overrides: Partial<Client> = {}) {
  function storage() {
    const values = new Map<string, string>()
    return {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => values.set(key, value),
      removeItem: (key: string) => values.delete(key),
    }
  }
  const location = { search: '', pathname: '/', hash: '' }
  Object.assign(globalThis, {
    location, localStorage: storage(), sessionStorage: storage(),
    history: { replaceState: (_: unknown, _title: string, url: string) => { location.search = url } },
  })
  let s!: ReturnType<typeof useChat>
  const api = {
    draft: async (id: string) => draftFor(id),
    history: async (id: string) => ({ turns: [turnFor(id)], before: null }),
    chats: async () => ({ items: [a, b, c] }),
    runtime: async () => ({}),
    batches: async () => ({ items: [] }),
    ...overrides,
  } as Client
  const renderer = createRenderer({ createComment: () => ({}), insert() {}, remove() {} } as never)
  const app = renderer.createApp({
    setup() {
      s = useChat(api)
      return () => null
    },
  })
  app.mount({})
  return { s, unmount: () => app.unmount() }
}

test('late rename updates the sidebar while the selected chat retains its own editor and panel', async () => {
  const rename = deferred<Chat>()
  const saved: string[] = []
  const { s, unmount } = mount({
    rename: () => rename.promise,
    async save(sid, value) {
      saved.push(sid)
      return { ...value, revision: value.revision + 1 }
    },
  })
  try {
    await s.init()
    s.setPanel('rename')
    const pending = s.rename('A renamed')
    await s.open(b)
    s.setPanel('batch')
    rename.resolve({ ...a, title: 'A renamed' })
    await pending
    assert.equal(s.current.value?.id, b.id)
    assert.equal(location.search, '?chat=B')
    assert.equal(s.rows.value[0].chat_id, b.id)
    assert.equal(s.draft.value?.token, b.id)
    assert.equal(s.panel.value, 'batch')
    assert.equal(s.chats.value[0].title, 'A renamed')

    s.edit({ text: 'saved to B' })
    await s.open(a)
    assert.deepEqual(saved, [b.id])
    assert.equal(s.current.value?.title, 'A renamed')
  } finally {
    unmount()
  }
})

test('navigation requested during a draft write commits only the last selected chat', async () => {
  const save = deferred<Draft>()
  const { s, unmount } = mount({ save: () => save.promise })
  try {
    await s.init()
    s.edit({ text: 'draft in A' })
    const pendingB = s.open(b)
    const pendingC = s.open(c)
    save.resolve({ ...draftFor(a.id), text: 'draft in A', revision: 1 })
    await Promise.all([pendingB, pendingC])
    assert.equal(s.current.value?.id, c.id)
    assert.equal(location.search, '?chat=C')
    assert.equal(s.rows.value[0].chat_id, c.id)
    assert.equal(s.draft.value?.token, c.id)
  } finally {
    unmount()
  }
})

test('an older list snapshot keeps an acknowledged title and can still discover a new chat', async () => {
  const list = deferred<{ items: Chat[] }>()
  let reads = 0
  const { s, unmount } = mount({
    chats: () => ++reads === 1 ? Promise.resolve({ items: [a, b] }) : list.promise,
    rename: async () => ({ ...a, title: 'updated A' }),
  })
  try {
    await s.init()
    const reload = s.reload()
    await s.rename('updated A')
    list.resolve({ items: [c, a, b] })
    await reload
    assert.equal(s.current.value?.title, 'updated A')
    assert.deepEqual(s.chats.value.map((chat) => chat.id), [c.id, a.id, b.id])
  } finally {
    unmount()
  }
})

test('initial loading preserves global settings and an explicitly targeted delete dialog', async () => {
  for (const panel of ['batch', 'delete'] as const) {
    const pending = deferred<{ items: Chat[] }>()
    const { s, unmount } = mount({ chats: () => pending.promise })
    try {
      const opening = s.init()
      s.setPanel(panel)
      pending.resolve({ items: [a] })
      await opening
      assert.equal(s.current.value?.id, a.id)
      assert.equal(s.panel.value, panel)
    } finally {
      unmount()
    }
  }
})

test('reloading after a failed autosave restores the matching local draft', async () => {
  const { s, unmount } = mount({ save: async () => { throw Error('write failure') } })
  try {
    await s.init()
    s.edit({ text: 'unsaved draft' })
    await assert.rejects(s.open(b), /write failure/)
    await s.reload()
    assert.equal(s.current.value?.id, a.id)
    assert.equal(s.draft.value?.text, 'unsaved draft')
    assert.ok(sessionStorage.getItem('gc:unsaved:A'))
  } finally {
    unmount()
  }
})

test('a conflicting saved revision remains visible while the divergent local draft is retained', async () => {
  const serverDraft = { ...draftFor(a.id), revision: 1, text: 'newer remote draft' }
  const { s, unmount } = mount({ draft: async () => serverDraft })
  try {
    const localDraft = { ...draftFor(a.id), text: 'divergent local draft' }
    sessionStorage.setItem('gc:unsaved:A', JSON.stringify(localDraft))
    await s.init()
    assert.equal(s.draft.value?.text, 'newer remote draft')
    assert.match(s.error.value, /服务端草稿版本已变化/)
    s.edit({ text: 'latest edit' })
    assert.equal(sessionStorage.getItem('gc:draft-conflict:A'), JSON.stringify(localDraft))
  } finally {
    unmount()
  }
})

test('a stale turn response cannot overwrite the durable answer recovered after a stream error', async () => {
  const pendingStop = deferred<Turn>()
  const reading = deferred<Turn>()
  const completed = { ...turnFor(a.id), cursor: 8, state: 'completed', result_hash: 'proof', answer: 'final' } as Turn
  const running = { ...turnFor(a.id), cursor: 2, state: 'running', watching: true, can_stop: true, draft: 'start' } as Turn
  let receive!: (event: TurnEvent) => void
  let stopped = 0
  let reads = 0
  const { s, unmount } = mount({
    history: async () => ({ turns: [running], before: null }),
    turn() { ++reads; return reading.promise },
    stop: () => pendingStop.promise,
    events(_id, _cursor, callback) {
      receive = callback
      return () => { ++stopped }
    },
  })
  try {
    await s.init()
    const control = s.control(running, 'stop')
    receive({ kind: 'draft', sequence: 3, text: 'part' })
    receive({ kind: 'draft', sequence: 3, text: 'part' })
    assert.equal(s.rows.value[0].draft, 'startpart')
    receive({ kind: 'error', sequence: 3, text: 'test failure' })
    assert.equal(s.reconnecting.value, true)
    reading.resolve(completed)
    await reading.promise
    pendingStop.resolve(running)
    await control
    assert.equal(reads, 1)
    assert.equal(stopped, 1)
    assert.equal(s.reconnecting.value, false)
    assert.equal(s.rows.value[0].answer, 'final')
  } finally {
    unmount()
  }
})

test('REST errors preserve the HTTP status and show safe messages for other failures', async (t) => {
  const assertError = async (code: string, message: RegExp, status?: number) => {
    await assert.rejects(client.chats(), (e) => e instanceof ApiError &&
      e.code === code && e.status === status && message.test(e.message))
  }
  t.mock.method(globalThis, 'fetch', async () => Response.json(
    { code: 'storage_busy', message: '请稍后再试。' }, { status: 503 },
  ))
  await assertError('storage_busy', /请稍后再试/, 503)
  t.mock.restoreAll()
  t.mock.method(globalThis, 'fetch', async () => new Response('private backend detail', { status: 500 }))
  await assertError('http', /HTTP 500/, 500)
  t.mock.restoreAll()
  t.mock.method(globalThis, 'fetch', async () => new Response('malformed success'))
  await assertError('invalid_response', /无法读取/, 200)
  t.mock.restoreAll()
  t.mock.method(globalThis, 'fetch', async () => { throw Error('underlying socket failure') })
  await assertError('connection', /无法连接/)
})

test('a permanent REST error stops progress retries and exposes its business message', async () => {
  const running = { ...turnFor(a.id), state: 'running', watching: true } as Turn
  let receive!: (event: TurnEvent) => void
  let reads = 0
  const { s, unmount } = mount({
    history: async () => ({ turns: [running], before: null }),
    async turn() {
      ++reads
      throw new ApiError('turn_missing', '轮次不存在。', 404)
    },
    events(_id, _cursor, callback) {
      receive = callback
      return () => {}
    },
  })
  try {
    await s.init()
    receive({ kind: 'error', sequence: 0, text: '进度失败' })
    await new Promise((resolve) => setTimeout(resolve, 350))
    assert.equal(reads, 1)
    assert.equal(s.reconnecting.value, false)
    assert.equal(s.error.value, '轮次不存在。')
  } finally {
    unmount()
  }
})
