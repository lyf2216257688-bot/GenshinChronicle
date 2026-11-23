import type { components } from './generated.ts'

type Schema = components['schemas']
export type Chat = Schema['Chat']
export type Mode = Chat['mode']
export type Turn = Schema['Turn']
export type Draft = Schema['Draft']
export type Batch = Schema['Batch']
export type Citation = Schema['Citation']
export type Feedback = Schema['Feedback']
export type Grant = Schema['Grant']
export type ReportInput = Schema['Report']
export type Source = Schema['Source']
export type Trace = Schema['Trace']
export type TurnEvent = Schema['TurnEvent']
export type Runtime = Schema['Runtime']
export type LiveSettings = Schema['LiveSettings']

export class ApiError extends Error {
  code: string
  status?: number
  constructor(code: string, message: string, status?: number) {
    super(message)
    this.code = code
    this.status = status
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  let response: Response
  try {
    response = await fetch('/api/v1' + path, {
      method,
      headers: { 'Content-Type': 'application/json', 'X-GC-Client': 'local-chat-v1' },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch {
    throw new ApiError('connection', '无法连接本机服务。请确认服务已启动。')
  }
  const data = await response.json().catch(() => null)
  if (!response.ok) {
    const code = typeof data?.code === 'string' ? data.code : 'http'
    const message = typeof data?.message === 'string' && data.message
      ? data.message : `本机服务请求失败（HTTP ${response.status}）。`
    throw new ApiError(code, message, response.status)
  }
  if (data === null || typeof data !== 'object')
    throw new ApiError('invalid_response', '本机服务返回了无法读取的响应。', response.status)
  return data as T
}

const id = encodeURIComponent
const apiRevision: Runtime['api_revision'] = 4
const eventKinds = new Set(['stage', 'draft', 'refresh', 'settled', 'error'])
export const client = {
  async runtime() {
    const runtime = await request<Runtime>('GET', '/runtime')
    if (runtime.api_revision !== apiRevision)
      throw new ApiError('service_outdated', '聊天页面和后台版本不一致。请停止旧服务，再从项目目录启动并刷新页面。')
    return runtime
  },
  saveLiveSettings: (data: LiveSettings) => request<Runtime>('PUT', '/live-settings', data),
  clearLiveSettings: () => request<Runtime>('DELETE', '/live-settings'),
  chats: () => request<Schema['Chats']>('GET', '/chats'),
  create: (mode: Mode) => request<Chat>('POST', '/chats', { mode }),
  rename: (sid: string, title: string) => request<Chat>('PATCH', '/chats/' + id(sid), { title }),
  deleteChat: (sid: string) => request<Schema['Chats']>('DELETE', '/chats/' + id(sid)),
  history: (sid: string, before?: number) =>
    request<Schema['Page']>(
      'GET',
      '/chats/' + id(sid) + '/turns' + (before ? '?before=' + before : ''),
    ),
  draft: (sid: string) => request<Draft>('GET', '/chats/' + id(sid) + '/draft'),
  save: (sid: string, draft: Draft) => request<Draft>('PUT', '/chats/' + id(sid) + '/draft', draft),
  submit: (sid: string, draft: Draft) =>
    request<Turn>('POST', '/chats/' + id(sid) + '/turns', draft),
  turn: (tid: string) => request<Turn>('GET', '/turns/' + id(tid)),
  stop: (tid: string) => request<Turn>('POST', '/turns/' + id(tid) + '/stop'),
  resume: (tid: string) => request<Turn>('POST', '/turns/' + id(tid) + '/resume'),
  source: (c: Citation) =>
    request<Source>(
      'GET',
      '/turns/' + id(c.turn_id) + '/packets/' + id(c.packet_id) + '/citations/' + id(c.citation_id),
    ),
  trace: (tid: string) => request<Trace>('GET', '/turns/' + id(tid) + '/trace'),
  report: (tid: string, data: ReportInput) =>
    request<Feedback>('POST', '/turns/' + id(tid) + '/feedback', data),
  reports: () => request<Schema['Reports']>('GET', '/feedback'),
  batches: () => request<Schema['Batches']>('GET', '/batches'),
  openBatch: (data: Grant) => request<Batch>('POST', '/batches', data),
  closeBatch: (bid: string) => request<Batch>('POST', '/batches/' + id(bid) + '/close'),
  events(
    tid: string,
    after: number,
    receive: (e: TurnEvent) => void,
    connection: (online: boolean) => void,
  ) {
    const stream = new EventSource('/api/v1/turns/' + id(tid) + '/events?after=' + after)
    stream.onopen = () => connection(true)
    stream.onerror = () => connection(false)
    stream.onmessage = (e) => {
      let event: TurnEvent
      try {
        event = JSON.parse(e.data) as TurnEvent
        if (!event || !eventKinds.has(event.kind) || typeof event.text !== 'string' ||
            !Number.isSafeInteger(event.sequence) || event.sequence < 0 ||
            (event.state != null && event.state !== 'running')) throw Error('Invalid progress event')
      } catch {
        connection(false)
        return
      }
      receive(event)
    }
    return () => stream.close()
  },
}
export type Client = typeof client
