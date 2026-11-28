// Run with the installed Windows DeepSeek Harness executable in Electron Node mode.
// Load only its Cordis registry, prompt registry, tool service and MCP bridge.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { createRequire } = require("node:module");
const { createHash } = require("node:crypto");

async function main() {
  const [python, auditName, release, benchmarkPath, sourceRoot] = process.argv.slice(2);
  assert(path.isAbsolute(python) && fs.existsSync(python), "Supply the Generation Python executable");
  assert(/^[A-Za-z0-9_-]{1,48}$/.test(auditName), "Supply a fresh audit name");
  assert(process.versions.electron, "Use the installed DeepSeek Harness executable");
  const repo = process.cwd();
  assert(!release || /^20[0-9]{6}$/.test(release), "Supply a registered release key");
  const parent = path.join(repo, release ? "data/generation/mcp-live-v1" : "data/generation/mcp-offline-v1");
  fs.mkdirSync(parent, { recursive: true });
  const output = path.join(parent, auditName);
  fs.mkdirSync(output);
  const fromDSH = createRequire(path.join(path.dirname(process.execPath), "resources/app.asar/dsh/package.json"));
  const { Context } = fromDSH("@deepseek-ai/cordis");
  const systemPrompt = fromDSH("@deepseek-ai/dsh-system-prompt").default;
  const tools = fromDSH("@deepseek-ai/dsh-tools").default;
  const bridge = fromDSH("@deepseek-ai/dsh-mcp-client");
  const timeout = release ? 600000 : 60000;
  const args = ["-X", "utf8", "-B", "-m", release ? "genshin_corpus.mcp_live" : "genshin_corpus.mcp_offline",
    "--repo", repo, "--audit-name", auditName];
  if (release) args.push("--release", release);
  const hostConfig = {
    transport: "stdio", serverName: "gc", cwd: repo, command: python,
    args,
    env: { PYTHONPATH: sourceRoot ?? path.join(repo, "src") }, startupTimeoutMs: 20000,
    toolCallTimeoutMs: timeout, failOnStartupError: true, reconnect: { enabled: false },
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
      async function call(name, args, signal = AbortSignal.timeout(timeout)) {
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
  if (release) {
    const report = benchmarkPath
      ? await benchmarkLive(connected, release, JSON.parse(fs.readFileSync(benchmarkPath, "utf8")), output)
      : await verifyLive(connected, release);
    report.host_bridge_version = fromDSH("@deepseek-ai/dsh-mcp-client/package.json").version;
    report.bridge_sha256 = createHash("sha256").update(fs.readFileSync(fromDSH.resolve("@deepseek-ai/dsh-mcp-client"))).digest("hex");
    report.node = process.versions.node;
    report.electron = process.versions.electron;
    const target = path.join(output, "dsh-client.json");
    fs.writeFileSync(target, JSON.stringify(report, null, 2) + "\n", { flag: "wx" });
    console.log(target);
    return;
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

async function collect(first, nextPage) {
  let page = first.projection;
  if (page.body !== undefined) return { body: page.body, pages: 1 };
  const digest = page.projection_sha256;
  const total = page.total_chars;
  const parts = [page.partial_json_text];
  while (page.next_offset !== null) {
    const offset = page.next_offset;
    page = (await nextPage(offset)).projection;
    assert.equal(page.offset, offset);
    assert.equal(page.projection_sha256, digest);
    parts.push(page.partial_json_text);
  }
  const text = parts.join("");
  assert.equal([...text].length, total);
  return { body: JSON.parse(text), pages: parts.length };
}

async function benchmarkLive(connected, release, { question, gap, search_text }, output) {
  return connected(async (call, tools) => {
    const scope = await call("get_scope", {});
    assert.equal(scope.new_studies.release, release);
    assert.equal(scope.remote_calls, 0);
    const steps = [
      ["cold_open", "open_study", { study: "cold", request_id: "open", question }],
      ["warm_open", "open_study", { study: "warm", request_id: "open", question }],
      ["warm_follow", "follow_query", { study: "warm", request_id: "follow", parent_query: "original", gap, search_text }],
    ];
    const samples = [];
    for (const [stage, tool, args] of steps) {
      const tick = performance.now();
      const result = await call(tool, args);
      const seconds = (performance.now() - tick) / 1000;
      assert.equal(result.binding, scope.new_studies.binding);
      const packet = { study: args.study, packet_ref: result.packet_ref };
      const projection = await collect(result, offset => call("get_packet", { ...packet, offset }));
      const citations = await call("check_answer", {
        ...packet, answer: result.citation_ids.map(id => `[${id}]`).join(" ") || "无查证结果",
      });
      assert.equal(citations.status, result.citation_ids.length ? "PASS" : "MISSING");
      const row = { stage, tool, args, seconds, result, projection, citations };
      fs.writeFileSync(path.join(output, stage + ".json"), JSON.stringify(row, null, 2), { flag: "wx" });
      samples.push({ stage, seconds, pages: projection.pages, packet: result.packet_id });
    }
    return { release, scope, tools, samples, generation_calls: 0 };
  });
}

async function verifyLive(connected, release) {
  const study = { study: "dsh-live" };
  const start = { ...study, request_id: "open", question: "伊涅芙在成为现在的伊涅芙之前，源自哪条古龙，她分出的意志曾用什么称号？" };
  const interrupted = { ...study, request_id: "cancel", parent_query: "original", gap: "检查另一个原文出处",
    search_text: "伊涅芙 角色语音 纳塔 最初的记忆" };
  let final;
  const report = await connected(async (call, tools) => {
    assert.equal((await call("get_scope", {})).new_studies.release, release);
    const tick = performance.now();
    const first = await call("open_study", start);
    const openSeconds = (performance.now() - tick) / 1000;
    const pageArgs = { ...study, packet_ref: first.packet_ref };
    const initial = await collect(first, offset => call("get_packet", { ...pageArgs, offset }));
    assert(initial.body.length > 0);
    const candidates = await call("browse_candidates", { ...study, query: "original" });
    const candidate = candidates.rows[0];
    assert(candidate);
    const followTime = performance.now();
    const follow = await call("follow_query", { ...study, request_id: "origin", parent_query: "original",
      gap: "补齐明晨之镜与悖谬的关系，优先查伊涅芙来历语音和人物故事",
      search_text: "伊涅芙 想要了解伊涅芙 其五 明晨之镜 一个念头 人物故事 悖谬",
      evidence_handles: [candidate.handle],
    });
    const followSeconds = (performance.now() - followTime) / 1000;
    assert.deepEqual(follow.query_ids, ["original", "origin"]);
    const more = await call("browse_candidates", { ...study, query: "origin" });
    const source = await call("read_source", { ...study, request_id: "read", handle: more.rows[0].handle, scope: "parent" });
    const expanded = await collect(source, offset => call("get_read", { ...study, read_ref: source.read_ref, offset }));
    assert(expanded.body);
    final = await call("assemble_packet", { ...study, request_id: "assemble" });
    const packet = { ...study, packet_ref: final.packet_ref };
    const output = await collect(final, offset => call("get_packet", { ...packet, offset }));
    assert(output.body.length > 0);
    const bound = await call("check_answer", { ...packet, answer: "[E01]" });
    assert.equal(bound.status, "PASS");
    await assert.rejects(call("check_answer", { ...pageArgs, answer: "[E01]" }), /Foreign or superseded Packet/);
    await assert.rejects(call("check_answer", { ...packet, answer: "[E999]" }));
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 3500);
    try {
      await assert.rejects(call("follow_query", interrupted, controller.signal));
      assert(controller.signal.aborted);
    } finally {
      clearTimeout(timer);
    }
    return { tools, release, binding: final.binding, generation_calls: 0, open_seconds: openSeconds,
      follow_seconds: followSeconds, first_packet: first.packet_id, final_packet: final.packet_id,
      first_packet_pages: initial.pages, final_packet_pages: output.pages,
      first_packet_chars: first.projection.total_chars, final_packet_chars: final.projection.total_chars,
      parent_pages: expanded.pages, parent_chars: source.projection.total_chars, parent_receipt: source.receipt_id,
      valid_citation: bound.sources.E01, stale_and_unknown_citations_rejected: true };
  });
  await connected(async call => {
    const committed = await call("request_status", { ...study, request_id: "assemble" });
    assert.deepEqual(committed, { status: "committed", result: final });
    const stopped = await call("request_status", { ...study, request_id: interrupted.request_id });
    assert.equal(stopped.status, "no_committed_result");
    assert.deepEqual((await call("get_study", study)).queries.map(item => item.query.query_id), ["original", "origin"]);
    report.cancellation = stopped;
    report.exact_recovery = true;
  });
  return report;
}

main().catch(error => { console.error(error); process.exitCode = 1; });
