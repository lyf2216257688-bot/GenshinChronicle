import { test, expect } from '@playwright/test'
import { mkdtemp, mkdir, rm } from 'node:fs/promises'
import { createServer } from 'node:net'
import { once } from 'node:events'
import { execFileSync, spawn, type ChildProcess } from 'node:child_process'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..')
const scratch = path.join(repo, 'tmp', 'browser-chat')
const python = path.join(repo, '.local/runtime-py314/generation/Scripts/python.exe')
const env = {
  ...process.env,
  PYTHONPATH: path.join(repo, 'src'),
  PYTHONUTF8: '1',
  DEEPSEEK_API_KEY: '',
  LANGSMITH_TRACING: 'false',
}
let root: string, port: number, url: string, server: ChildProcess
async function start() {
  const windows = process.platform === 'win32'
  const entry = windows ? ['/d', '/c', 'Start-GC-Chat.cmd'] : ['-B', '-m', 'genshin_corpus.local_chat']
  server = spawn(windows ? 'cmd.exe' : python, [...entry, '--no-browser', '--root', root, '--port', String(port)],
    { cwd: repo, env, windowsHide: true, stdio: 'ignore' })
  await expect
    .poll(
      async () => {
        try {
          return (await fetch(url + 'api/v1/runtime')).status
        } catch {
          return 0
        }
      },
      { timeout: 20000 },
    )
    .toBe(200)
}
async function stop() {
  if (!server || server.exitCode !== null) return
  // Windows venv launchers spawn the interpreter. Terminate just this test's process tree.
  if (process.platform === 'win32') {
    const killer = spawn('taskkill', ['/PID', String(server.pid), '/T', '/F'], {
      windowsHide: true,
      stdio: 'ignore',
    })
    await once(killer, 'exit')
  } else {
    const closed = once(server, 'exit')
    server.kill('SIGTERM')
    await closed
  }
}
test.beforeAll(async () => {
  await mkdir(scratch, { recursive: true })
  root = await mkdtemp(path.join(scratch, 'case-'))
  const socket = createServer()
  socket.listen(0, '127.0.0.1')
  await once(socket, 'listening')
  const address = socket.address()
  if (!address || typeof address === 'string') throw Error('no port')
  port = address.port
  await new Promise<void>((r) => socket.close(() => r()))
  url = 'http://127.0.0.1:' + port + '/'
  await start()
})
test.afterAll(async () => {
  await stop()
  if (
    root &&
    path.dirname(path.resolve(root)) === scratch &&
    path.basename(root).startsWith('case-')
  )
    await rm(path.toNamespacedPath(root), { recursive: true, force: true, maxRetries: 3 })
})
test('ordinary browser workflow across two grants and a service restart', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', (e) => errors.push(e.message))
  await page.goto(url)
  await page.getByRole('button', { name: '体验离线演示', exact: true }).click()
  const input = page.getByRole('textbox', { name: '提问', exact: true })
  await input.fill('旧前提是甲。')
  await input.dispatchEvent('keydown', { key: 'Enter', isComposing: true })
  await expect(page.locator('.turn')).toHaveCount(0)
  await page.getByRole('button', { name: '发送', exact: true }).click()
  const first = page.locator('.turn').first()
  await expect(first.locator('.inline-cite').first()).toBeVisible({ timeout: 20000 })
  await first.locator('.inline-cite').first().click()
  await expect(page.getByText('父级原文', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '反馈此引用' }).click()
  await page.getByLabel('说明', { exact: true }).fill('请复查这句引文。')
  await page.getByRole('button', { name: '保存反馈' }).click()
  await first.getByRole('button', { name: '纠正这条' }).click()
  await input.fill('撤回甲，仅讨论这段原文。')
  await input.press('Enter')
  await expect(page.locator('.turn').nth(1).locator('.answer')).toBeVisible({ timeout: 20000 })
  await page.getByRole('button', { name: '批次设置', exact: true }).click()
  await page.getByRole('button', { name: '关闭此批次' }).click()
  await expect(page.getByRole('button', { name: '开启批次', exact: true })).toBeVisible()
  await page.getByRole('button', { name: '关闭详情' }).click()
  await input.fill('那刚才这个判断依据是什么？')
  await expect
    .poll(async () => {
      const sid = new URL(page.url()).searchParams.get('chat')
      return (await (await fetch(url + 'api/v1/chats/' + sid + '/draft')).json()).text
    })
    .toBe('那刚才这个判断依据是什么？')
  await stop()
  await start()
  await page.reload()
  await expect(input).toHaveValue('那刚才这个判断依据是什么？')
  await page.getByRole('button', { name: '开启问答批次' }).click()
  await page.getByRole('button', { name: '开启批次', exact: true }).click()
  await page.getByRole('button', { name: '发送', exact: true }).click()
  await expect(page.locator('.turn').nth(2).locator('.answer')).toBeVisible({ timeout: 20000 })
  await page.getByRole('button', { name: '反馈记录', exact: true }).click()
  await page.getByRole('button', { name: '回到原回答及引用' }).click()
  await expect(page.locator('.source-quote')).toBeVisible()
  await page.getByRole('button', { name: '关闭详情' }).click()
  await input.fill('停止此次生成')
  await input.press('Enter')
  await page.getByRole('button', { name: '停止本轮' }).last().click()
  await expect(page.locator('.turn').last().getByText('已停止', { exact: true })).toBeVisible({
    timeout: 20000,
  })
  expect(errors).toEqual([])
})

test('launcher reuses the right service and an interrupted turn resumes only on click', async ({
  page,
}) => {
  if (process.platform === 'win32') {
    // The server is already running. A launcher selecting the wrong root or opening
    // another instance would exit with an error or time out.
    execFileSync(
      'cmd.exe',
      ['/d', '/c', 'Start-GC-Chat.cmd', '--no-browser', '--port', String(port), '--root', root],
      { cwd: repo, env, windowsHide: true, timeout: 12000 },
    )
  }
  await page.goto(url)
  await page.getByRole('button', { name: '离线演示', exact: true }).click()
  const input = page.getByRole('textbox', { name: '提问', exact: true })
  await expect(page.getByRole('heading', { name: '新对话', level: 1, exact: true })).toBeVisible()
  await expect(input).toBeVisible()
  const sid = new URL(page.url()).searchParams.get('chat')
  const head = async () =>
    (await (await fetch(url + 'api/v1/chats/' + sid + '/turns')).json()).turns[0]
  const count = async () =>
    (await (await fetch(url + 'api/v1/batches')).json()).items.find(
      (b: { mode: string }) => b.mode === 'MOCK',
    ).requests
  const prior = await count()
  // Reproduce an actual worker interruption before dispatch with the existing
  // exclusive execution lock. Releasing it never schedules a request.
  const hold = spawn(
    python,
    [
      '-B',
      '-u',
      '-c',
      [
        'from pathlib import Path',
        'import sys',
        'from genshin_corpus.model_calls import locked',
        'with locked(Path(sys.argv[1]) / "locks/application.lock"):',
        '    print("ready", flush=True)',
        '    sys.stdin.readline()',
      ].join('\n'),
      root,
    ],
    { cwd: repo, env, windowsHide: true, stdio: ['pipe', 'pipe', 'inherit'] },
  )
  try {
    await once(hold.stdout, 'data')
    await input.fill('请在重开软件后继续本轮')
    await input.press('Enter')
    await expect(page.getByRole('button', { name: '继续本轮', exact: true })).toBeVisible({
      timeout: 20000,
    })
  } finally {
    const closed = once(hold, 'exit')
    hold.stdin.end('\n')
    await closed
  }
  const interrupted = await head()
  expect(interrupted.state).toBe('interrupted')
  await stop()
  await start()
  await page.reload()
  await expect(page.getByRole('button', { name: '继续本轮', exact: true })).toBeVisible()
  expect(await count()).toBe(prior)
  await page.getByRole('button', { name: '继续本轮', exact: true }).click()
  await expect(page.locator('.turn .answer')).toBeVisible({ timeout: 20000 })
  expect((await head()).batch_id).toBe(interrupted.batch_id)
  expect((await head()).id).toBe(interrupted.id)
  expect(await count()).toBe(prior + 1)
  const replay = await (
    await fetch(url + 'api/v1/turns/' + interrupted.id + '/events?after=0')
  ).text()
  const events = replay
    .split('\n')
    .filter((line) => line.startsWith('data: '))
    .map((line) => JSON.parse(line.slice(6)))
  expect(events.some((event) => event.kind === 'draft')).toBe(true)
  expect(events.at(-1).kind).toBe('settled')
})

test('unbound numbers, invalid citations and claims with insufficient support', async ({
  page,
}) => {
  const sid = execFileSync(python, ['-B', '-m', 'tests.local_chat.citation_cases', root], {
    cwd: repo,
    env,
    windowsHide: true,
    encoding: 'utf8',
    timeout: 30000,
  }).trim()
  await page.goto(url + '?chat=' + sid)
  const turns = page.locator('.turn')
  await expect(turns).toHaveCount(3)
  await expect(turns.nth(0)).toContainText('本回答没有已绑定的原文引用')
  await expect(turns.nth(0).locator('.inline-cite')).toHaveCount(0)
  await expect(turns.nth(1)).toContainText('引用与本轮证据不匹配')
  await expect(turns.nth(1).locator('.answer')).toHaveCount(0)
  await turns.nth(2).locator('.inline-cite').click()
  await expect(page.locator('.source-quote')).toBeVisible()
  await expect(page.locator('.panel')).toContainText('判断它能否支持回答中的结论')
  await page.getByText('来源与支持范围', { exact: true }).click()
  await expect(page.locator('.panel')).toContainText('NOT_EVALUATED')
  await page.getByRole('button', { name: '反馈此引用' }).click()
  await page.getByLabel('说明', { exact: true }).fill('原文不足以支持此身份推断。')
  await page.getByRole('button', { name: '保存反馈' }).click()
  await expect(page.getByRole('status').filter({ hasText: '反馈已保存。' })).toBeVisible()
  await page.getByRole('button', { name: '反馈记录', exact: true }).click()
  await expect(page.locator('.feedback-card').first()).toContainText('原文不足以支持此身份推断。')
})
