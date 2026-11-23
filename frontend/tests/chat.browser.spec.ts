import { test, expect } from '@playwright/test'
import { mkdtemp, mkdir, rm } from 'node:fs/promises'
import { createServer } from 'node:net'
import { once } from 'node:events'
import { execFileSync, spawn, type ChildProcess } from 'node:child_process'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..')
const scratch = path.join(repo, '.local', 'tmp', 'browser-chat')
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

  await page.getByRole('button', { name: '关闭详情' }).click()
  await page.setViewportSize({ width: 390, height: 844 })
  const navToggle = page.getByRole('button', { name: '打开会话导航' })
  await navToggle.click()
  await expect(page.getByRole('button', { name: '收起导航' })).toBeFocused()
  await page.keyboard.press('Escape')
  await expect(navToggle).toBeFocused()
  await expect(navToggle).toHaveAttribute('aria-expanded', 'false')

  const citation = turns.nth(2).locator('.inline-cite')
  await citation.click()
  await expect(citation).toHaveAttribute('aria-expanded', 'true')
  await expect(page.getByRole('button', { name: '关闭详情' })).toBeFocused()
  await expect(page.locator('.source-quote')).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.getByRole('complementary', { name: '引用原文' })).toHaveCount(0)
  await expect(citation).toBeFocused()
  await expect(citation).toHaveAttribute('aria-expanded', 'false')
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(390)
})

test('real connection settings are saved locally before a batch can be opened', async ({ page }) => {
  test.skip(process.platform !== 'win32', 'Windows DPAPI')
  await page.goto(url)
  await page.getByRole('button', { name: '新建对话', exact: true }).click()
  await expect(page.getByRole('heading', { name: '新对话', level: 1, exact: true })).toBeVisible()
  const composer = page.getByRole('textbox', { name: '提问', exact: true })
  await expect(composer).toBeVisible()
  await page.getByRole('button', { name: '批次设置', exact: true }).click()
  const panel = page.getByRole('complementary', { name: '批次设置' })
  await expect(panel.getByRole('combobox')).toHaveValue('LIVE')
  await expect(panel.getByText('应用数据目录', { exact: true })).toBeVisible()
  const endpoint = panel.getByLabel('请求地址', { exact: true })
  const apiKey = panel.getByLabel('API Key', { exact: true })
  await expect(apiKey).toHaveAttribute('type', 'password')
  await endpoint.fill('https://api.deepseek.com')
  await apiKey.fill('offline-browser-fixture')
  await panel.getByRole('button', { name: '保存配置' }).click()
  await expect(panel.getByText('已保存在本机', { exact: true })).toBeVisible()
  await expect(panel.getByRole('button', { name: '开启批次', exact: true })).toBeDisabled()
  await expect(panel.getByRole('checkbox', { name: /我授权本批次/ })).not.toBeChecked()
  await page.reload()
  await expect(composer).toBeVisible()
  await page.getByRole('button', { name: '批次设置', exact: true }).click()
  await expect(panel.getByRole('combobox')).toHaveValue('LIVE')
  await expect(panel.getByText('已保存在本机', { exact: true })).toBeVisible()
  await panel.getByText('真实问答配置', { exact: true }).click()
  await expect(apiKey).toHaveValue('')
  await panel.getByRole('button', { name: '移除本机 Key' }).click()
  await expect(panel.getByText('待配置', { exact: true })).toBeVisible()
})

test('an old backend is reported as a version mismatch', async ({ page }) => {
  await page.route('**/api/v1/runtime', async (route) => {
    const response = await route.fetch()
    const current = await response.json()
    const { product, instance, issues, key_configured, live_model, tokens_per_request } = current
    await route.fulfill({ response, json: { product, instance, issues, key_configured, live_model, tokens_per_request } })
  })
  await page.goto(url)
  const button = page.getByRole('button', { name: '批次设置', exact: true })
  await button.click()
  const panel = page.getByRole('complementary', { name: '批次设置' })
  await panel.getByRole('combobox').selectOption('LIVE')
  await expect(panel).toContainText('聊天页面和后台版本不一致')
  await expect(panel.getByText('应用数据目录', { exact: true })).toHaveCount(0)
  await expect(panel).not.toContainText('无法使用 Windows 账号加密')
  await expect(panel.getByRole('button', { name: '开启批次' })).toHaveCount(0)
})

test('deleting a sidebar chat needs confirmation and survives restart', async ({ page, request }) => {
  const titles = ['待删除会话 A', '待删除会话 B']
  const [a, b] = await Promise.all(titles.map(async (title) => {
    const response = await request.post(url + 'api/v1/chats', {
      data: { mode: 'LIVE', title },
      headers: { 'X-GC-Client': 'local-chat-v1' },
    })
    expect(response.ok()).toBe(true)
    return (await response.json()).id as string
  }))
  await page.goto(url + '?chat=' + a)
  const target = page.getByRole('button', { name: '删除会话：' + titles[1] })
  const confirm = page.getByRole('complementary', { name: '删除会话' })
  await target.click()
  await confirm.getByRole('button', { name: '取消', exact: true }).click()
  await expect(target).toBeFocused()
  await target.click()
  await confirm.getByRole('button', { name: '确认删除', exact: true }).click()
  await expect(target).toHaveCount(0)
  await expect(page.getByRole('heading', { name: titles[0], level: 1 })).toBeVisible()
  await page.setViewportSize({ width: 390, height: 844 })
  await page.getByRole('button', { name: '打开会话导航' }).click()
  await page.getByRole('button', { name: '删除会话：' + titles[0] }).click()
  await confirm.getByRole('button', { name: '确认删除', exact: true }).click()
  await expect(page.getByText('会话已删除。')).toBeVisible()
  await expect(page).not.toHaveURL(url + '?chat=' + a)
  await stop()
  await start()
  await page.goto(url + '?chat=' + b)
  await expect(page.getByRole('heading', { name: titles[1], level: 1 })).toHaveCount(0)
  await page.getByRole('button', { name: '打开会话导航' }).click()
  for (const title of titles)
    await expect(page.getByRole('button', { name: '删除会话：' + title })).toHaveCount(0)
})
