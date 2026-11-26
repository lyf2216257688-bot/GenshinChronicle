// Run with the installed Windows DeepSeek Harness executable in Electron Node mode.
// Load only its Cordis registry, prompt registry, tool service and MCP bridge.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { createRequire } = require("node:module");
const { createHash } = require("node:crypto");

async function main() {
  const [python, auditName] = process.argv.slice(2);
  assert(path.isAbsolute(python) && fs.existsSync(python), "Supply the Generation Python executable");
  assert(/^[A-Za-z0-9_-]{1,48}$/.test(auditName), "Supply a fresh audit name");
  assert(process.versions.electron, "Use the installed DeepSeek Harness executable");
  const repo = process.cwd();
  const parent = path.join(repo, "data/generation/mcp-offline-v1");
  fs.mkdirSync(parent, { recursive: true });
  const output = path.join(parent, auditName);
  fs.mkdirSync(output);
  const fromDSH = createRequire(path.join(path.dirname(process.execPath), "resources/app.asar/dsh/package.json"));
  const { Context } = fromDSH("@deepseek-ai/cordis");
  const systemPrompt = fromDSH("@deepseek-ai/dsh-system-prompt").default;
  const tools = fromDSH("@deepseek-ai/dsh-tools").default;
  const bridge = fromDSH("@deepseek-ai/dsh-mcp-client");
  const hostConfig = {
    transport: "stdio", serverName: "gc", cwd: repo, command: python,
    args: ["-X", "utf8", "-B", "-m", "genshin_corpus.mcp_offline", "--repo", repo, "--audit-name", auditName],
    env: { PYTHONPATH: path.join(repo, "src") }, startupTimeoutMs: 20000,
    toolCallTimeoutMs: 60000, failOnStartupError: true, reconnect: { enabled: false },
  };
  async function connected(run) {
    const ctx = new Context();
    const fibers = [];
    try {
      for (const [plugin, config] of [[systemPrompt], [tools], [bridge, hostConfig]]) {
        const fiber = ctx.plugin(plugin, config);
        fibers.push(fiber);
        await fiber.await();
      }
      async function call(name, args, signal = AbortSignal.timeout(60000)) {
        const tool = ctx.tools.get("mcp__gc__" + name);
        assert(tool, name);
        const exec = { signal };
        const value = await tool.execute(args, exec);
        const fallback = tool.output.render(args, value);
        const override = await tool.projectContent(exec, { isError: false, value, content: fallback });
        const blocks = override ?? fallback;
        assert.equal(blocks.length, 1);
        const projected = JSON.parse(blocks[0].text);
        assert.deepEqual(projected, value.structuredContent);
        return projected;
      }
      return await run(call, ctx.tools.definitions().map(t => t.name));
    } finally {
      for (const fiber of fibers.reverse()) await fiber.dispose();
    }
  }
  const start = { study: "dsh-study", case: "Q068", request_id: "start" };
  const interrupted = { study: "cancel-probe", case: "Q001", request_id: "open" };
  const report = await connected(async (call, names) => {
    const first = await call("open_study", start);
    assert(first.first_packet_parity);
    const expected = first.projection.body;
    let pages = 0, offset = 0, text = "";
    do {
      const result = await call("get_packet", { study: start.study, packet_ref: first.packet_ref, offset });
      const page = result.projection;
      assert.equal(page.offset, offset);
      assert.equal(page.projection_sha256, first.projection.projection_sha256);
      text += page.partial_json_text ?? JSON.stringify(page.body);
      pages += 1;
      offset = page.next_offset;
    } while (offset !== null);
    assert.deepEqual(JSON.parse(text), expected);
    const choice = first.available_queries[0];
    const follow = await call("follow_query", {
      study: start.study, request_id: "follow", query: choice.query, gap: choice.gap,
      parent_query: choice.parent_queries[0], evidence_handles: choice.evidence_handles,
    });
    const checked = await call("check_answer", { study: start.study, packet_ref: follow.packet_ref, answer: "[E01]" });
    assert.equal(checked.status, "PASS");
    await assert.rejects(call("check_answer", {
      study: start.study, packet_ref: first.packet_ref, answer: "[E01]",
    }), /Foreign or superseded Packet/);
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 30);
    let cancelOutcome = "completed_before_abort";
    try {
      await call("open_study", interrupted, controller.signal);
    } catch (error) {
      if (!controller.signal.aborted) throw error;
      cancelOutcome = "host_reported_error_after_abort";
    } finally {
      clearTimeout(timer);
    }
    return {
      host_bridge_version: fromDSH("@deepseek-ai/dsh-mcp-client/package.json").version,
      bridge_sha256: createHash("sha256").update(fs.readFileSync(fromDSH.resolve("@deepseek-ai/dsh-mcp-client"))).digest("hex"),
      node: process.versions.node, electron: process.versions.electron, tools: names,
      binding: first.binding, first_packet: first.packet_id, follow_packet: follow.packet_id,
      first_packet_pages: pages, projection_chars: first.projection.total_chars,
      complete_projected_evidence: true, cited_e01: checked.status, rejected_stale_e01: true,
      cancellation: cancelOutcome, generation_calls: 0,
    };
  });
  await connected(async call => {
    const prior = await call("request_status", { study: start.study, request_id: "start" });
    const replay = await call("open_study", start);
    assert.equal(prior.status, "committed");
    assert.deepEqual(prior.result, replay);
    assert.equal(replay.packet_id, report.first_packet);
    const observed = await call("request_status", { study: interrupted.study, request_id: interrupted.request_id });
    assert(["committed", "no_committed_result"].includes(observed.status));
    report.after_cancellation = observed.status;
    if (observed.last_failure_type) report.cancellation_failure = observed.last_failure_type;
  });
  report.committed_recovery = "PASS";
  const target = path.join(output, "dsh-client.json");
  fs.writeFileSync(target, JSON.stringify(report, null, 2) + "\n", { flag: "wx" });
  console.log(target);
}

main().catch(error => { console.error(error); process.exitCode = 1; });
