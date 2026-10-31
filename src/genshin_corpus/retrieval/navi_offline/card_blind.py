"""Prepare and run source-identical Card comparisons; no generation by default.

Control archives and both providers' results stay outside the CLI workspace.
Only the existing Card contract and source-local validation are reused here.
"""
import argparse
from dataclasses import asdict
from copy import deepcopy
import json
from pathlib import Path
import secrets
import subprocess
import time

from . import card_generation as generation, cards_v2 as v2, metro_chat, requests
from ... import model_calls
from .common import copy, digest, read, require, seal, sha, verify, write
from .sources import Sources, prepare_sources

VERSION = 'p05-card-independent-blind-1'
GEMINI = 'gemini-3.8-flash-high'
DENY = [f'{action}(*)' for action in
        ('read_file', 'write_file', 'command', 'read_url', 'execute_url', 'mcp')]
SETTINGS = {'allowNonWorkspaceAccess': False, 'enableTerminalSandbox': True,
            'toolPermission': 'strict', 'disableSlashCommands': True,
            'permissions': {'allow': [], 'ask': [], 'deny': DENY}}
RUBRIC = '''# 文档级 Card 匿名评审

只依据共同交付原文判断，不使用模型身份、历史结果或模型自评分。
逐项记录具体主张、引文、支持范围、遗漏、反例及不确定性；无需指定胜者。
每个维度可记 0（严重问题）/1（部分合格）/2（可靠）/UNKNOWN，并附证据。

- 人物、身份、行动与事件归属；代词、时序和分支条件。
- 有原文支撑的具体隐含线索及关键遗漏；主题相似本身不算发现。
- 外部知识污染、未经支持的身份同一、阵营与因果。
- 观察、解释和假说的区分；合理解释是否被过度排除。
- 传说、戏剧、小说和角色证词是否被误写为确定历史。
- 全文叙事是否连贯综合；阅读范围与未读限制是否准确。
- 关键主张是否由引文及必要上下文真正支持。
- 阅读向量投影是否保留具体人物、行动、事件与潜在线索；本包不重新计算向量。

Schema、逐字引文及来源机械验收单独记录，不替代以上语义判断。
逐字引文可能不支持整条判断，必须检查主张与证据之间的对应。
仅双方有效的文档作完整配对比较；单方有效只单独评价；双方无有效Card不强行评分。
缺失或机械失败不记作语义零分。总体报告保留全部资料分母，并另报完整配对覆盖率，
不得将成功子集的语义表现外推到全部资料。每篇A/B独立随机，不能跨篇把A视作同一模型。
所有材料保持 INACTIVE / NOT_REVIEWED。独立辅助意见不构成人工批准。
'''


def text_file(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf8', newline='\n') as f:
        f.write(text)


def code_identity():
    # A source snapshot also guards the reused validator/transport implementations.
    return {p.name: sha(p) for p in sorted(Path(__file__).parent.glob('*.py'))} | model_calls.code_identity()


def prepare(root, canonical, ru_manifest, selection, *, workspace):
    root = Path(root)
    require(not (root/'prepared').exists(), 'Blind preparation already exists')
    prepared = root/'prepared'
    documents = prepare_sources(prepared/'sources', canonical, ru_manifest,
                                [x['id'] for x in selection])
    visible = prepared/'materials'
    text_file(visible/'TASK.md', '仅根据指定请求中实际交付的证据生成一张文档级剧情 Card。'
              '不读取其他文件、不使用工具、网络、历史会话或训练记忆补充。'
              '长资料须综合全部交付段落；未知与未读边界如实说明。输出遵循给定合同。\n')
    write(visible/'card.schema.json', v2.SCHEMA)
    text_file(visible/'card-prompt.txt', generation.PROMPT)
    sources = Sources(prepared/'sources')
    cases = []
    try:
        for n, selected in enumerate(selection, 1):
            source = selected['id']; case = f'T{n:02d}'
            plan = v2.reading_plan(sources, source)
            batches = list(v2.read_batches(sources, source, plan, 16000, 180000, True))
            require(len(batches) == 1, 'Benchmark requires one full selected-range document request')
            _, view, evidence = batches[0]
            for q in evidence:
                sources.verify_quote(q)
            view['scope']['document_objective'] = '综合全部交付段落，生成一张文档级 Card；不是阅读批次目录'
            view['scope']['coverage_accounting'] = {
                'selected_chars': sum(len(q['text']) for q in evidence),
                'omitted_representation_chars': sum(b-a for u in plan['units'] for a,b in u['unread_ranges']),
                'upstream_skips': len(plan['upstream_skips']),
                'boundary': '未交付表示不自动等于未读整章；不保证全 Canonical 语义覆盖'}
            wire = generation.wire(view, generation.DEEPSEEK)
            model_view, aliases = generation.aliases(view)
            prompt = wire['messages'][0]['content']+'\n\n实际交付原文:\n'+wire['messages'][1]['content']
            folder = prepared/'cases'/case
            write(folder/'job.json', {'case':case, 'source_id':source, 'view':view,
                                     'evidence':evidence, 'wire':wire})
            write(visible/case/'input.json', model_view)
            text_file(visible/case/'request.txt', prompt)
            write(visible/case/'evidence-map.json', {'aliases':aliases, 'evidence':evidence})
            write(visible/case/'reading-plan.json', plan)
            write(visible/case/'retrieval-units.json', sources.rows(source))
            doc = sources.documents[source]
            copy(sources.root/doc['canonical_file'], visible/case/'canonical.json')
            cases.append({'case':case, 'source_id':source, 'title':doc['title'],
                          'stratum':selected['stratum'],
                          'selected_chars':sum(len(q['text']) for q in evidence),
                          'canonical_visible_chars':sum(len(r['retrieval_visible_text']) for r in sources.rows(source)),
                          'ru_count':len(sources.rows(source)), 'requests_per_provider':1,
                          'input_sha256':sha(visible/case/'request.txt'),
                          'semantic_messages_sha256':digest(wire['messages']),
                          'canonical_sha256':doc['canonical_sha256']})
        write(visible/'case-manifest.json', {'version':VERSION, 'cases':cases,
              'scope':'SELECTED_RU_RANGES; source plans retain all omissions; no expected answers'})
        seal(visible)
        for p in Path(__file__).parent.glob('*.py'):
            copy(p, prepared/'execution-code'/p.name)
        model_calls.snapshot(prepared/'execution-code')
        write(prepared/'config.json', {'version':VERSION, 'workspace':str(Path(workspace).resolve()),
              'gemini_model':GEMINI, 'deepseek_model':'deepseek-v4.1-flash',
              'cases':cases, 'settings':SETTINGS, 'code':code_identity(), 'call_layer':model_calls.VERSION,
              'schema_sha256':digest(v2.SCHEMA), 'semantic_prompt_sha256':digest(generation.PROMPT),
              'max_invocations_per_provider':len(cases), 'send_authorized':False,
              'gemini_internal_request_count':'UNKNOWN_NOT_CLI_INVOCATION_COUNT',
              'os_isolation':'NOT_CERTIFIED', 'navigation_status':'INACTIVE',
              'human_acceptance':'NOT_REVIEWED'})
        seal(prepared)
    finally:
        sources.close()
    return {'prepared_sha256':sha(prepared/'complete.json'), 'cases':cases,
            'generation_calls':0, 'send_authorized':False}


def export_workspace(root):
    root = Path(root); verify(root/'prepared')
    config = read(root/'prepared/config.json'); target = Path(config['workspace'])
    require(not target.exists(), 'CLI materials already exist; no overwrite')
    for p in (root/'prepared/materials').rglob('*'):
        if p.is_file():
            copy(p, target/p.relative_to(root/'prepared/materials'))
    verify(target)
    return {'workspace':str(target), 'files_match':True, 'generation_calls':0}


def gemini_command(cli, workspace, settings_path, log_path):
    # Fresh one-user-turn process. stdin avoids the Windows command-line length limit.
    return [str(cli), '--new-project', '--model', GEMINI, '--sandbox',
            '--settings', str(settings_path), '--disable-slash-commands',
            '--input-format', 'stream-json', '--output-format', 'stream-json',
            '--json-schema', str(Path(workspace)/'card.schema.json'),
            '--print-timeout', '15m', '--log-file', str(log_path)]


def parse_gemini(raw, view, evidence):
    events = [requests.candidate.strict_json(line) for line in raw.splitlines() if line.strip()]
    finals = [e['result'] for e in events if e.get('event') == 'result']
    require(len(finals) == 1, 'CLI must return exactly one terminal result')
    final = finals[0]
    require(final.get('status') == 'SUCCESS' and final.get('num_turns') == 1, 'CLI turn failed or contaminated')
    # Never substitute intermediate reasoning/tool output for the final answer.
    text = final.get('response')
    require(isinstance(text, str), 'Missing CLI final response')
    parsed = requests.candidate.strict_json(text)
    require(parsed == final.get('structured_output'), 'CLI structured/final payload differs')
    decoded, conversions = generation.decode(text, view, evidence)
    value = generation.validate_value(decoded, view, evidence)
    return final, value, conversions


def validate_saved(root, provider, case):
    folder = result_folder(root,provider,case)
    result = read(folder/'result.json')
    require(result['state'] == 'CONTENT_VERIFIED', 'Failed/UNKNOWN request must never be resent')
    return result


def result_folder(root,provider,case):
    root=Path(root); original=root/'runs'/provider/case
    verify(original)
    if read(original/'result.json')['state']=='CONTENT_VERIFIED':
        return original
    recovered=root/'recoveries'/provider/case
    if recovered.exists():
        verify(recovered)
        lineage=read(recovered/'lineage.json')
        require(lineage.get('revision_sha256') in [sha(p/'complete.json') for p in revision_dirs(root)],
                'Unknown recovery code revision')
        require(lineage=={
            'original_complete_sha256':sha(original/'complete.json'),
            'prepared_sha256':sha(root/'prepared/complete.json'),
            'revision_sha256':lineage['revision_sha256']},'Recovery lineage changed')
        check_revision(root)
        return recovered
    return original


def revision_dirs(root):
    return sorted(p for p in Path(root).glob('runtime-revision*') if p.is_dir())


def check_revision(root):
    root=Path(root); revisions=revision_dirs(root)
    require(revisions,'Code changed since preparation; explicit revision required')
    for p in revisions:verify(p)
    revision=revisions[-1]
    record=read(revision/'revision.json')
    require(record['prepared_sha256']==sha(root/'prepared/complete.json'),'Revision preparation differs')
    require(record['code']==code_identity(),'Runtime revision code changed')
    for name,expected in record['code'].items():
        require(sha(revision/'execution-code'/name)==expected,'Revision code snapshot changed')
    for name,expected in record['existing_records'].items():
        require(sha(root/name)==expected,'Historical execution record changed')
    return record


def adopt_revision(root):
    """Append an explicit code migration; never reseal preparation or reservations."""
    root=Path(root);verify(root/'prepared');config=read(root/'prepared/config.json')
    current=code_identity()
    revisions=revision_dirs(root)
    for p in revisions:
        verify(p)
        for name,expected in read(p/'revision.json')['existing_records'].items():
            require(sha(root/name)==expected,'Historical revision dependency changed')
    if revisions and read(revisions[-1]/'revision.json')['code']==current:return check_revision(root)
    require(set(current)==set(config['code']),'Unexpected module-set change')
    changed=[name for name in current if current[name]!=config['code'][name]]
    require(changed and set(changed)<= {'card_blind.py','card_generation.py'},'Unreviewed runtime changes')
    for name,expected in config['code'].items():
        require(sha(root/'prepared/execution-code'/name)==expected,'Old code snapshot changed')
    for case in config['cases']:
        job=read(root/'prepared/cases'/case['case']/'job.json')
        require(job['wire']==generation.wire(job['view'],generation.DEEPSEEK),'Semantic wire changed')
    require(config['settings']==SETTINGS and config['schema_sha256']==digest(v2.SCHEMA)
            and config['semantic_prompt_sha256']==digest(generation.PROMPT),'Task or CLI policy changed')
    existing={}
    for p in revisions:
        existing[(p/'complete.json').relative_to(root).as_posix()]=sha(p/'complete.json')
    for p in sorted((root/'recoveries').glob('*/*/complete.json')):
        verify(p.parent);existing[p.relative_to(root).as_posix()]=sha(p)
    for path in sorted((root/'runs').glob('*/execution.json')):
        existing[path.relative_to(root).as_posix()]=sha(path)
    for folder in sorted((root/'runs').glob('*/*')):
        if folder.is_dir():
            verify(folder)
            existing[(folder/'complete.json').relative_to(root).as_posix()]=sha(folder/'complete.json')
    record={'version':'blind-newline-recovery-1','prepared_sha256':sha(root/'prepared/complete.json'),
            'old_code':config['code'],'code':current,'changed_files':changed,'existing_records':existing,
            'policy':'OFFLINE_VALIDATION_ONLY; original inputs, wire, fingerprints and reservations unchanged'}
    revision=root/('runtime-revision' if not revisions else f'runtime-revision-{len(revisions)+1:04d}')
    write(revision/'revision.json',record)
    for p in Path(__file__).parent.glob('*.py'):copy(p,revision/'execution-code'/p.name)
    model_calls.snapshot(revision/'execution-code')
    seal(revision)
    return record


def recover_gemini(root,case):
    """Revalidate an already completed native response without launching any process."""
    root=Path(root);verify(root/'prepared');check_revision(root)
    original=root/'runs/gemini'/case;verify(original)
    old=read(original/'result.json')
    require(old['state']!='CONTENT_VERIFIED','Successful original must remain untouched')
    require(old.get('exit_code')==0,'No proven successful CLI termination')
    target=root/'recoveries/gemini'/case
    if target.exists():return validate_saved(root,'gemini',case)
    job=read(root/'prepared/cases'/case/'job.json')
    final,value,conversions=parse_gemini((original/'stdout.raw.jsonl').read_text(encoding='utf8'),
                                        job['view'],job['evidence'])
    require(final.get('usage')==old['provider_reported_usage'],'Original usage differs')
    result=deepcopy(old)
    result.update(state='CONTENT_VERIFIED',content_validation={'status':'PASS'},
                  recovery_mode='OFFLINE_ORIGINAL_RESPONSE_NO_NEW_REQUEST',
                  historical_failure={'type':old.get('failure_type'),'reason':old.get('reason')},
                  protocol_validation={'status':'PASS_CLI_ENVELOPE_NOT_HTTP_ATTESTATION'})
    for key in ('reason','failure_type'):result.pop(key,None)
    counts=final.get('usage',{})
    require(all(type(counts.get(k)) is int and counts[k]>=0 for k in
                ('input_tokens','output_tokens','total_tokens')),'Unknown usage blocks recovery')
    result['conservative_accounting_tokens']=max(counts['total_tokens'],counts['input_tokens']+counts['output_tokens'])
    sources=Sources(root/'prepared/sources')
    try:
        for q in job['evidence']:sources.verify_quote(q)
        part={'job_id':digest(['gemini',case,sha(original/'stdout.raw.jsonl')]),
              'value':value,'authority':'LIVE_OFFLINE_RECOVERED_NOT_HUMAN_REVIEWED'}
        card=v2.make_card(sources,job['source_id'],'document',None,part,job['evidence'],
                         plan=read(root/'prepared/materials'/case/'reading-plan.json'))
    finally:sources.close()
    write(target/'lineage.json',{'original_complete_sha256':sha(original/'complete.json'),
          'prepared_sha256':sha(root/'prepared/complete.json'),
          'revision_sha256':sha(revision_dirs(root)[-1]/'complete.json')})
    write(target/'envelope.json',final);text_file(target/'final.txt',final['response'])
    write(target/'conversions.json',conversions);write(target/'value.json',value)
    write(target/'card.json',card);write(target/'result.json',result);seal(target)
    return result


def isolate_completed_failure(root,case):
    """Allow independent work only after proving this sent request completed/accounted."""
    root=Path(root);verify(root/'prepared');check_revision(root)
    original=root/'runs/gemini'/case;verify(original);old=read(original/'result.json')
    require(old['state']=='FAILED_OR_UNKNOWN_NO_RESEND' and old.get('exit_code')==0,
            'Only completed content failure can be isolated')
    events=[requests.candidate.strict_json(x) for x in (original/'stdout.raw.jsonl').read_text(encoding='utf8').splitlines() if x.strip()]
    finals=[e['result'] for e in events if e.get('event')=='result']
    require(len(finals)==1,'Uncertain terminal state cannot be isolated')
    final=finals[0]
    require(final.get('status')=='SUCCESS' and final.get('num_turns')==1 and
            requests.candidate.strict_json(final['response'])==final.get('structured_output'),
            'Uncertain protocol cannot be isolated')
    counts=final.get('usage',{})
    require(counts==old.get('provider_reported_usage') and
            all(type(counts.get(k)) is int and counts[k]>=0 for k in
                ('input_tokens','output_tokens','total_tokens')),'Unknown usage blocks dispatch')
    target=root/'isolated/gemini'/case
    result=old|{'state':'CONTENT_REJECTED_ISOLATED_NO_RESEND',
                'content_validation':{'status':'FAIL'},
                'conservative_accounting_tokens':max(counts['total_tokens'],counts['input_tokens']+counts['output_tokens']),
                'navigation_status':'INACTIVE','human_acceptance':'NOT_REVIEWED'}
    write(target/'lineage.json',{'original_complete_sha256':sha(original/'complete.json'),
          'prepared_sha256':sha(root/'prepared/complete.json')})
    write(target/'result.json',result);seal(target)
    return result


def existing_result(root,provider,case):
    root=Path(root);folder=result_folder(root,provider,case)
    result=read(folder/'result.json')
    if result['state']=='CONTENT_VERIFIED':return result
    isolated=root/'isolated'/provider/case
    if not isolated.exists():return validate_saved(root,provider,case)
    verify(isolated)
    require(read(isolated/'lineage.json')=={
        'original_complete_sha256':sha(folder/'complete.json'),
        'prepared_sha256':sha(root/'prepared/complete.json')},'Isolation lineage changed')
    result=read(isolated/'result.json')
    require(result['state']=='CONTENT_REJECTED_ISOLATED_NO_RESEND','Invalid isolation state')
    return result


def authorize_independent_deepseek(root):
    """Explicit user-granted exception for this five-case experiment, never a retry."""
    root=Path(root);verify(root/'prepared');check_revision(root)
    config=read(root/'prepared/config.json');original=root/'runs/deepseek/T01'
    verify(original);result=read(original/'result.json')
    require(result.get('http_status')==524 and result.get('calls')==1 and
            result.get('provider_reported_usage')=='UNKNOWN','Unexpected prior failure')
    cases=config['cases']
    require([c['case'] for c in cases]==['T01','T02','T03','T04','T05'],'Unexpected bounded cohort')
    path=root/'deepseek-independent-dispatch.json'
    permit={'prepared_sha256':sha(root/'prepared/complete.json'),
        'code_sha256':digest(code_identity()),'original_T01_complete_sha256':sha(original/'complete.json'),
        'allowed_new_cases':[c['case'] for c in cases[1:]],'maximum_total_case_requests':5,
        'model':'deepseek-v4.1-flash','provider':'deepseek',
        'authorization':'User explicitly allows remaining four independent requests with prior T01 usage UNKNOWN',
        'unknown_usage_policy':'Retain UNKNOWN, never zero; cumulative token total cannot be certified. Fixed case-count bound applies.',
        'automatic_retry':False}
    if path.exists():require(read(path)==permit,'Bounded dispatch permit changed')
    else:write(path,permit)
    return permit


def bounded_deepseek_result(root,case):
    folder=result_folder(root,'deepseek',case) # Verify seal; UNKNOWN is never resent.
    return read(folder/'result.json')


def run(root, provider, *, execute=False, ack=None, cli=None, max_requests=5, token_budget=500000,
        independent_unknown=False, call_policy=None):
    root = Path(root).resolve(); verify(root/'prepared')
    config = read(root/'prepared/config.json')
    if config['code'] != code_identity():check_revision(root)
    workspace = Path(config['workspace']); verify(workspace)
    require(sha(workspace/'complete.json') == sha(root/'prepared/materials/complete.json'), 'CLI workspace differs')
    require(provider in {'gemini','deepseek'}, 'Unknown provider')
    if independent_unknown:
        require(provider=='deepseek','Bounded exception is DeepSeek-only')
        permit=read(root/'deepseek-independent-dispatch.json')
        require(permit['prepared_sha256']==sha(root/'prepared/complete.json') and
                permit['code_sha256']==digest(code_identity()) and
                permit['original_T01_complete_sha256']==sha(root/'runs/deepseek/T01/complete.json') and
                permit['allowed_new_cases']==['T02','T03','T04','T05'] and
                permit['maximum_total_case_requests']==5,'Bounded permit identity changed')
    require(type(max_requests) is int and 0 < max_requests <= 5, 'Invalid invocation bound')
    if not execute:
        return {'provider':provider, 'dry_run':True, 'calls':0,
                'prepared_sha256':sha(root/'prepared/complete.json'), 'cases':config['cases']}
    require(ack == sha(root/'prepared/complete.json'), 'Explicit execution and exact preparation hash required')
    require(type(token_budget) is int and token_budget > 0, 'Finite cumulative token budget required')
    execution=root/'runs'/provider/'execution.json'
    policy=call_policy or model_calls.Policy()
    identity={'prepared_sha256':ack,'token_budget':token_budget,'model':config[provider+'_model']}
    if config.get('call_layer')==model_calls.VERSION:identity['call_policy']=asdict(policy)
    if execution.exists():
        require(read(execution)==identity,'Resume execution identity changed')
    else:
        write(execution,identity)
    shared=config.get('call_layer')==model_calls.VERSION and not (root/'deepseek-independent-dispatch.json').exists()
    batch=model_calls.Batch(root/'call-budgets'/provider,max_requests=config['max_invocations_per_provider'],max_tokens=token_budget,dispatch_limit=max_requests) if shared else None
    existing = list((root/'runs'/provider).glob('*/reservation.json'))
    require(not independent_unknown or len(existing)<=5,'Bounded dispatch count exceeded')
    used = 0;unknown_usage=[]
    for path in existing:
        if shared and not (path.parent/'complete.json').exists():
            if read(path).get('state')!='SHARED_RESUMABLE':result_folder(root,provider,path.parent.name)
            unknown_usage.append(path.parent.name);continue
        result = read(result_folder(root,provider,path.parent.name)/'result.json') if independent_unknown or shared else existing_result(root, provider, path.parent.name)
        if type(result.get('conservative_accounting_tokens')) is int:used+=result['conservative_accounting_tokens']
        else:
            require(independent_unknown or shared,'Unknown usage blocks further dispatch')
            unknown_usage.append(path.parent.name)
    new_calls = 0; outputs = []
    for case in config['cases']:
        case_id = case['case']; folder = root/'runs'/provider/case_id
        if folder.exists() and (not shared or (folder/'complete.json').exists()):
            outputs.append(read(result_folder(root,provider,case_id)/'result.json') if independent_unknown or shared else existing_result(root, provider, case_id)); continue
        if batch and batch.accounting()['request_reservations']>=config['max_invocations_per_provider']:break
        if new_calls >= max_requests:
            break
        if independent_unknown:
            require(case_id in permit['allowed_new_cases'] and len(existing)+new_calls<5,
                    'No authorization for this additional request')
        job = read(root/'prepared/cases'/case_id/'job.json')
        require(shared or used + len(json.dumps(job['wire'],ensure_ascii=False).encode()) + 12288 <= token_budget,
                'Cumulative token budget lacks next-request allowance')
        folder.mkdir(parents=True, exist_ok=shared)
        if not (folder/'reservation.json').exists():write(folder/'reservation.json', {'case':case_id, 'provider':provider,
              'input_sha256':case['input_sha256'], 'state':'SHARED_RESUMABLE' if shared else 'RESERVED_NEVER_AUTO_RESEND',
              'prepared_sha256':ack, 'token_budget':token_budget})
        try:
            if provider == 'deepseek':
                result = _deepseek(folder, job, call_batch=batch,call_policy=policy) if shared else _deepseek(folder,job)
            else:
                result = _gemini(folder, job, workspace, cli, case['input_sha256'],call_batch=batch,call_policy=policy)
        except model_calls.Deferred as exc:
            path=root/'deferred'/provider/case_id
            write(path/f'{len(list(path.glob("*.json"))):04d}.json',{'reason':str(exc),'state':'DEFERRED_NO_NEW_DISPATCH'})
            outputs.append({'state':'DEFERRED','case':case_id,'reason':str(exc)});continue
        except Exception as exc:
            result={'state':'FAILED_OR_UNKNOWN_NO_RESEND','failure_type':type(exc).__name__,
                    'provider_reported_usage':'UNKNOWN','actual_fee':'UNKNOWN',
                    'navigation_status':'INACTIVE','human_acceptance':'NOT_REVIEWED'}
        if result['state']=='CONTENT_VERIFIED':
            sources=Sources(root/'prepared/sources')
            try:
                value=read(folder/'value.json')
                part={'job_id':digest([provider,case_id,case['input_sha256']]),
                      'value':value,'authority':'LIVE_NOT_HUMAN_REVIEWED'}
                card=v2.make_card(sources,case['source_id'],'document',None,part,job['evidence'],
                     plan=read(workspace/case_id/'reading-plan.json'))
                write(folder/'card.json',card)
            finally:
                sources.close()
        new_calls += 1
        write(folder/'result.json', result); seal(folder)
        outputs.append(result)
        require(shared or independent_unknown or result['state'] == 'CONTENT_VERIFIED', 'Generation failed; raw evidence retained, no auto retry')
        if type(result.get('conservative_accounting_tokens')) is int:used+=result['conservative_accounting_tokens']
        else:
            require(shared or independent_unknown,'Observed usage UNKNOWN; stop')
            unknown_usage.append(case_id)
    return {'provider':provider, 'new_invocations':new_calls, 'results':outputs,
            'call_accounting':batch.accounting() if batch else None,
            'unknown_usage_cases':unknown_usage,'known_accounting_tokens':used,
            'total_token_accounting':'UNKNOWN' if unknown_usage else used,
            'actual_fee':'UNKNOWN', 'navigation_status':'INACTIVE', 'human_acceptance':'NOT_REVIEWED'}


def _deepseek(folder, job, *, call_batch=None,call_policy=None):
    from genshin_corpus.rag.navi_r1_experiment import environment
    env = environment()
    require(env.get('TOKENMETRO_BASE_URL','').rstrip('/') == requests.METRO_URL, 'Metro route differs')
    require(job['wire'] == generation.wire(job['view'], generation.DEEPSEEK), 'Prepared Card wire differs')
    result = metro_chat.execute(folder/'request', job['view'], job['evidence'],
             strategy=generation.DEEPSEEK, live_authorized=True,
             api_key=env.get('TOKENMETRO_API_KEY'),call_batch=call_batch,call_policy=call_policy)
    if result['state'] == 'CONTENT_VERIFIED':
        value = read(folder/'request/value.json'); write(folder/'value.json', value)
    return result


def _gemini_once(folder, job, workspace, cli, input_sha):
    require(cli and Path(cli).is_file(), 'Antigravity CLI unavailable')
    write(folder/'settings.json', SETTINGS)
    args = gemini_command(cli, workspace, folder/'settings.json', folder/'cli.log')
    prompt = (workspace/job['case']/'request.txt').read_text(encoding='utf8')
    require(sha(workspace/job['case']/'request.txt') == input_sha, 'CLI prompt changed')
    payload = json.dumps({'event':'user','message':{'content':prompt}}, ensure_ascii=False)+'\n'
    text_file(folder/'stdin.jsonl', payload)
    write(folder/'command.json', {'argv':args, 'cwd':str(workspace), 'fresh_process':True,
          'no_continue':True, 'one_user_event':True, 'native_internal_retries':'UNKNOWN'})
    result = {'state':'UNKNOWN', 'actual_fee':'UNKNOWN', 'provider_reported_usage':'UNKNOWN',
              'requested_model':GEMINI, 'reported_model':'UNKNOWN',
              'navigation_status':'INACTIVE','human_acceptance':'NOT_REVIEWED'}
    started = time.monotonic()
    try:
        with (folder/'stdout.raw.jsonl').open('xb') as out, (folder/'stderr.raw.txt').open('xb') as err:
            completed = subprocess.run(args, input=payload.encode('utf8'), cwd=workspace,
                                       stdout=out, stderr=err, timeout=960, check=False)
        result['exit_code']=completed.returncode
        final, value, conversions = parse_gemini((folder/'stdout.raw.jsonl').read_text(encoding='utf8'),
                                                job['view'], job['evidence'])
        result['provider_reported_usage'] = final.get('usage','UNKNOWN')
        text_file(folder/'final.txt',final['response'])
        require(completed.returncode == 0, 'CLI nonzero exit')
        write(folder/'envelope.json', final)
        write(folder/'conversions.json', conversions)
        write(folder/'value.json', value)
        # Native intermediate events are raw audit data, never Card evidence.
        events=[json.loads(x) for x in (folder/'stdout.raw.jsonl').read_text(encoding='utf8').splitlines() if x.strip()]
        reasoning=[e for e in events if any(k in json.dumps(e.get('step_update',{}))
                                           for k in ('thinking_delta','reasoning_delta','thinking_content'))]
        write(folder/'reasoning-events.json', {'events':reasoning,
              'boundary':'only CLI-exposed events; hidden reasoning UNKNOWN'})
        counts = result['provider_reported_usage']
        if isinstance(counts,dict) and all(type(counts.get(k)) is int and counts[k]>=0
                                          for k in ('input_tokens','output_tokens','total_tokens')):
            # Thinking/cache accounting may overlap; retain native counts without reinterpretation.
            result['conservative_accounting_tokens'] = max(counts['total_tokens'],
                                                         counts['input_tokens']+counts['output_tokens'])
        result.update(state='CONTENT_VERIFIED', content_validation={'status':'PASS'},
                      reported_model=final.get('model','UNKNOWN'),
                      protocol_validation={'status':'PASS_CLI_ENVELOPE_NOT_HTTP_ATTESTATION'},
                      exit_code=completed.returncode)
    except Exception as exc:
        result.update(state='FAILED_OR_UNKNOWN_NO_RESEND', failure_type=type(exc).__name__, reason=str(exc))
        # Failed content still retains any observed terminal usage, separate from validation.
        try:
            events=[json.loads(x) for x in (folder/'stdout.raw.jsonl').read_text(encoding='utf8').splitlines()]
            finals=[e['result'] for e in events if e.get('event')=='result']
            if len(finals)==1:
                result['provider_reported_usage']=finals[0].get('usage','UNKNOWN')
                if result.get('exit_code')==0 and finals[0].get('status')=='SUCCESS':
                    result['protocol_validation']={'status':'PASS_CLI_ENVELOPE_NOT_HTTP_ATTESTATION'}
                    result['content_validation']={'status':'FAIL'}
        except (ValueError,OSError,KeyError):
            pass
    result['elapsed_seconds'] = round(time.monotonic()-started,3)
    counts=result['provider_reported_usage']
    if isinstance(counts,dict) and all(type(counts.get(k)) is int and counts[k]>=0
                                     for k in ('input_tokens','output_tokens','total_tokens')):
        result['conservative_accounting_tokens']=max(counts['total_tokens'],counts['input_tokens']+counts['output_tokens'])
    result.update(calls=0,external_invocations=1,request_count='UNKNOWN',
                  native_internal_retries='UNKNOWN',stream_validation={'status':'EXTERNAL_PROGRAM_NOT_HTTP_ATTESTATION'})
    write(folder/'result.json',result);seal(folder)
    return result


def _gemini(folder,job,workspace,cli,input_sha,*,call_batch=None,call_policy=None):
    fp=digest([VERSION,GEMINI,input_sha,digest(v2.SCHEMA),SETTINGS])
    plan={'fingerprint':fp,'model':GEMINI,'input_sha256':input_sha,'schema_sha256':digest(v2.SCHEMA),'settings':SETTINGS}
    ledger=Path(requests.METRO_LEDGER).parent/'gemini-card-blind-ledger'
    result=model_calls.run(folder/'invocation',plan,ledger,
        lambda attempt,headers:_gemini_once(attempt,job,workspace,cli,input_sha),
        channel=['antigravity-cli',GEMINI],batch=call_batch,policy=call_policy,
        token_allowance=len((workspace/job['case']/'request.txt').read_bytes())+12288)
    for p in (folder/'invocation').iterdir():
        if p.is_file() and p.name not in {'plan.json','call.json','result.json','complete.json'}:
            target=folder/p.name
            if target.exists():require(sha(target)==sha(p),'Recovered CLI projection changed')
            else:copy(p,target)
    return result


def review_outcome(root,provider,label):
    """Anonymous mechanical disposition; raw native errors remain private."""
    root=Path(root); original=root/'runs'/provider/label
    if not original.exists():
        return {'status':'NOT_SENT','failure_categories':['NOT_SENT']},None,None
    if not (original/'complete.json').exists():
        return {'status':'UNKNOWN','failure_categories':['INCOMPLETE_OR_UNKNOWN']},None,None
    folder=result_folder(root,provider,label);result=read(folder/'result.json')
    if result['state']=='CONTENT_VERIFIED':
        value=deepcopy(read(folder/'value.json'));card=read(folder/'card.json')
        return {'status':'PASS','schema':'PASS','source_binding':'PASS','quotations':'PASS',
            'observations':len(value.get('source_observations',[])),
            'verified_anchors':sum(len(x.get('anchors',[])) for x in value.get('source_observations',[])),
            'reading_receipt_count':len(value.get('read_ids',[])), 'failure_categories':[]},value,card
    native=' '.join(str(result.get(k,'')) for k in ('reason','error_codes','assessment_failure','failure_type'))
    if type(result.get('http_status')) is int and result['http_status']>=400:
        categories=['TRANSPORT_OR_PROVIDER_FAILURE']
    elif any(s in native.casefold() for s in ('quotation','quote','speaker label')):
        categories=['QUOTATION_NOT_EXACT']
    elif any(s in native.casefold() for s in ('schema','validationerror','is not of type','required property')):
        categories=['SCHEMA_INVALID']
    elif result.get('protocol_validation',{}).get('status')=='FAIL':
        categories=['RESPONSE_PROTOCOL_INVALID']
    else:categories=['CONTENT_OR_PROTOCOL_NOT_ACCEPTED']
    return {'status':'FAIL','failure_categories':categories},None,None


def export_review(root, target):
    root=Path(root); target=Path(target); verify(root/'prepared')
    require(not target.exists(), 'Review export is create-only')
    require(not (root/'review-key.json').exists(),'Review already randomized; preserve the original assignment')
    config=read(root/'prepared/config.json'); mapping={};files={};cases=[]
    for case in config['cases']:
        label=case['case']; order=['gemini','deepseek']; secrets.SystemRandom().shuffle(order)
        mapping[label]={k:v for k,v in zip(('A','B'),order)}
        states={}
        for anon, provider in mapping[label].items():
            mechanical,value,card=review_outcome(root,provider,label);states[anon]=mechanical['status']
            mechanical.update(semantic_eligible=value is not None,semantic_score=None,
                              semantic_accuracy='NOT_EVALUATED',human_acceptance='NOT_REVIEWED',
                              navigation_status='INACTIVE')
            files[f'{label}/{anon}-mechanical.json']=mechanical
            if value is not None:
                # Preserve every semantic field; strip only provider execution metadata.
                files[f'{label}/{anon}.json']=value
                files[f'{label}/{anon}-reading.json']={'coverage':card['coverage'],
                    'limitations':card['limitations'],'whole_page_semantic_coverage':False}
                files[f'{label}/{anon}-projection.txt']=v2.projection(card)
        passed=sum(s=='PASS' for s in states.values())
        scope={2:'PAIRED_VALID',1:'SINGLE_VALID',0:'NO_VALID_CARD'}[passed]
        cases.append({k:v for k,v in case.items() if k in {'case','title','stratum','selected_chars'}}|
                     {'comparison_scope':scope,'mechanical_status':states})
        for name in ('input.json','evidence-map.json','reading-plan.json'):
            files[f'{label}/{name}']=read(root/'prepared/materials'/label/name)
        instructions='双方均有效，可作完整配对比较。' if passed==2 else '仅评价有效一方；缺失一方不记语义零分。' if passed==1 else '双方无有效Card，只记机械状态和原因，不作语义评分。'
        files[f'{label}/review.md']=RUBRIC+'\n'+instructions+'\n\n评审记录：\n\nA：\n\nB：\n'
    files['cases.json']={'cases':cases,'assignment':'independent random assignment per source',
        'cohorts':{scope:sum(c['comparison_scope']==scope for c in cases)
                   for scope in ('PAIRED_VALID','SINGLE_VALID','NO_VALID_CARD')},
        'total_documents':len(cases),'human_acceptance':'NOT_REVIEWED',
        'semantic_denominator_policy':'Paired judgments use paired-valid cases only; always report all-case coverage and failures separately.'}
    files['RUBRIC.md']=RUBRIC
    files['card-prompt.txt']=(root/'prepared/materials/card-prompt.txt').read_text(encoding='utf8')
    files['card.schema.json']=read(root/'prepared/materials/card.schema.json')
    files['projection-policy.json']={'version':v2.PROJECTION,'new_embedding_calls':0,
                                    'purpose':'Review text preservation; no retrieval-performance claim'}
    # Do not silently rewrite a model's semantic text if it identifies itself.
    for name,value in files.items():
        text=value if isinstance(value,str) else json.dumps(value,ensure_ascii=False)
        require(not any(token in (name+' '+text).casefold() for token in
                        ('gemini','deepseek','antigravity','tokenmetro','jizhi','qwen')),
                'Model identity appears in review material; no silent redaction')
    for name,value in files.items():
        if isinstance(value,str):text_file(target/name,value)
        else:write(target/name,value)
    seal(target)
    write(root/'review-key.json',{'review_complete_sha256':sha(target/'complete.json'),
          'assignment':mapping,'visibility':'PRIVATE_CONTROL_ONLY; reveal after review'})
    return {'review':str(target),'key':str(root/'review-key.json'), 'generation_calls':0}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('export-workspace');p.add_argument('--root',required=True)
    p=sub.add_parser('run');p.add_argument('--root',required=True)
    p.add_argument('--provider',choices=['gemini','deepseek'],required=True)
    p.add_argument('--execute-authorized',action='store_true');p.add_argument('--ack')
    p.add_argument('--cli',default='E:/Antigravity/CLI/agy.exe')
    p.add_argument('--max-requests',type=int,default=5);p.add_argument('--token-budget',type=int,default=500000)
    p.add_argument('--independent-unknown-authorized',action='store_true')
    p.add_argument('--call-policy',type=Path,help='Versioned shared retry policy JSON; not model-call authorization')
    p=sub.add_parser('export-review');p.add_argument('--root',required=True);p.add_argument('--target',required=True)
    p=sub.add_parser('adopt-revision');p.add_argument('--root',required=True)
    p=sub.add_parser('recover-gemini');p.add_argument('--root',required=True);p.add_argument('--case',required=True)
    p=sub.add_parser('isolate-gemini');p.add_argument('--root',required=True);p.add_argument('--case',required=True)
    p=sub.add_parser('authorize-independent-deepseek');p.add_argument('--root',required=True)
    args=parser.parse_args()
    if args.command=='export-workspace':result=export_workspace(args.root)
    elif args.command=='export-review':result=export_review(args.root,args.target)
    elif args.command=='adopt-revision':result=adopt_revision(args.root)
    elif args.command=='recover-gemini':result=recover_gemini(args.root,args.case)
    elif args.command=='isolate-gemini':result=isolate_completed_failure(args.root,args.case)
    elif args.command=='authorize-independent-deepseek':result=authorize_independent_deepseek(args.root)
    else:result=run(args.root,args.provider,execute=args.execute_authorized,ack=args.ack,
                    cli=args.cli,max_requests=args.max_requests,token_budget=args.token_budget,
                    independent_unknown=args.independent_unknown_authorized,
                    call_policy=model_calls.Policy(**read(args.call_policy)) if args.call_policy else None)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
