"""Opt-in single-case E2; E1 tools, immutable decisions, no active views."""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import time
from typing import Any, Mapping

from genshin_corpus.canonical.fingerprints import canonical_json_bytes, sha256_json
from genshin_corpus.collector.storage import atomic_write
from genshin_corpus.retrieval.navi_exploration import (
    LocalExplorationTools, ExplorationError, ExplorationBudgetExceeded, response_chars,
)
from .navi_e2_routes import e2_route, E2_ROUTES
from genshin_corpus.retrieval.semantic_sdk_runner import (
    _DoneTrackingStream, _JsonlArtifactWriter, _chunk_mapping, _stream_delta, _normalize_usage,
    _responses_event_parts, _ResponsesDecisionText, safe_rate_limit_retry, _retry_after,
    research_retry_classifier,
)
from .navi_experiment import _validate_action, _resolve_read, _save as _e1_save

HYPOTHESIS = "unverified_hypothesis_not_fact"
QUESTION = "从绘真关于遗失馆藏的陈述出发，调查白夜国人如何叙述海渊生活、蛇神到来与迁居海面的历史。寻找其他实际原文，区分角色证言、史料、小说、民间故事、传说和作者推测，说明哪些联系仍无法证实。只形成有来源的调查线索，不作确定历史结论。"
PROMPT = {
    "task": "你是单案例离线剧情材料调查员，自主选择检索词与后续来源，不依赖训练记忆补全原文。",
    "authority": "工具原文可回查；所有clue和memory均为unverified_hypothesis_not_fact。保持说话者、否定、条件、小说/传说/推测/史料不足的限定；同名和关键词只构成线索。页面攻略和编辑整理不等同游戏内正文。",
    "output": "仅输出JSON对象：{actions:[...],memory:{status:'unverified_hypothesis_not_fact',text:'有界工作记忆'},rationale:'选择或停止理由'}。每轮1至6个顺序动作，可组合search/read/clue/stop。",
    "tools": {
        "search": "{action:'search',query:自主选取的字符串(<=160字符),top_k:1..5}。本地Lexical，无Hybrid或网页。",
        "read": "{action:'read',unit_id:原样复制候选或seed提供的正式RU ID,max_chars:1..20000,neighbors:0或1}。两种上下文均使用unit_id引用来源；model_round是模型请求轮次，action_index是全局动作编号，均不是RU ID。只回读真实Canonical，截断可见。",
        "clue": "{action:'clue',text:<=1000字符的待核实线索,source_unit_ids:[已读RU]}。",
        "stop": "{action:'stop',reason:<=1000字符}。来源或预算足够时主动停止；不要为了凑轮数继续。",
    },
    "context": "seed自动提供；之后每轮只收到上一轮工具返回、已读来源定位、动作索引、上一轮工作记忆和剩余预算，需在memory保留必要线索。不能把局部读取当作整页已读。最后一轮应主动stop。",
}


SINGLE_ACTION_PROMPT = {
    "task": "继续同一有界研究问题，自主选择下一步。只输出一个很短的JSON动作，不要解释或总结。",
    "authority": PROMPT['authority'],
    "output": "直接输出search/read/clue/stop动作对象，不包actions/memory/rationale。read.unit_id原样复制候选或seed提供的正式RU ID。clue.text或stop.reason最多500字。无需凑齐动作，材料不足可注明并停止。",
    "tools": PROMPT['tools'],
    "limits": "一次只能一个动作。read后请在clue中注明材料属性、作者/说话者、推测与史料限制；不把候选片段当作完整已读来源。clue始终是unverified_hypothesis_not_fact。剩余轮数或材料足够时自主stop。",
}


RESEARCH_PROMPT = {
    "task": PROMPT["task"],
    "authority": PROMPT["authority"],
    "output": PROMPT["output"],
    "tools": {
        "search": PROMPT["tools"]["search"],
        "read": (
            "{action:'read',unit_id:原样复制候选或seed提供的正式RU ID,max_chars:1..2000000,neighbors:0或1}。"
            "两种上下文均使用unit_id引用来源；model_round是模型请求轮次，action_index是全局动作编号，均不是RU ID。只回读真实Canonical，截断可见。"
            "研究模式单次读取上限为2,000,000字符，推荐单次容量为4,000~8,000+字符；"
            "因读取结果包含完整出处(provenance)、定位与审计哈希等元数据包底（固定占用约2,000+字符），单次max_chars过小会导致元数据无法容纳；"
            "若工具返回错误read_budget_too_small及min_required_max_chars，须在后续动作中以max_chars >= min_required_max_chars自适应调整并重试读取。"
        ),
        "clue": PROMPT["tools"]["clue"],
        "stop": PROMPT["tools"]["stop"],
    },
    "context": (
        "seed自动提供；之后每轮只收到上一轮工具返回、已读来源定位、动作索引、上一轮工作记忆和剩余预算，需在memory保留必要线索。"
        "研究模式预算宽裕，若收到read_budget_too_small错误，应根据min_required_max_chars在后续动作调整容量重试。"
        "不能把局部读取当作整页已读。最后一轮应主动stop。"
    ),
}


RESEARCH_SINGLE_ACTION_PROMPT = {
    "task": SINGLE_ACTION_PROMPT["task"],
    "authority": PROMPT["authority"],
    "output": SINGLE_ACTION_PROMPT["output"],
    "tools": RESEARCH_PROMPT["tools"],
    "limits": (
        "一次只能一个动作。研究模式推荐单次read容量为4,000~8,000+字符（上限2,000,000字符），因出处与元数据包底固定占用约2,000+字符；"
        "若工具返回错误read_budget_too_small及min_required_max_chars，下一步应以max_chars >= min_required_max_chars自适应调整并重试读取。"
        "read后请在clue中注明材料属性、作者/说话者、推测与史料限制；不把候选片段当作完整已读来源。clue始终是unverified_hypothesis_not_fact。剩余轮数或材料足够时自主stop。",
    ),
}


def lineage_accounting(root):
    """Read frozen issued/terminal authority, never trust legacy accepted-only usage."""
    root=Path(root).resolve()
    state=json.loads((root/'session.json').read_text('utf-8'))
    if state['checkpoint_identity'] != sha256_json({k:v for k,v in state.items() if k!='checkpoint_identity'}):
        raise ExplorationError('parent checkpoint integrity')
    config=json.loads((root/'config.json').read_text('utf-8'))
    if sha256_json(config) != state['run_identity']: raise ExplorationError('parent config integrity')
    if (root/'artifact-manifest.json').exists():
        inventory=json.loads((root/'artifact-manifest.json').read_text('utf-8'))
        for row in inventory['files']:
            path=(root.parent/row['root']/row['path']).resolve()
            if not path.is_relative_to(root.parent) or sha256(path.read_bytes()).hexdigest()!=row['sha256']:
                raise ExplorationError('parent artifact inventory integrity')
    inherited=lineage_accounting(state['parent']['root']) if state.get('parent') else {'calls':0,'tokens':0,'reservations':0,'terminals':[]}
    calls,tokens,reservations=inherited['calls'],inherited['tokens'],inherited['reservations']
    terminals=list(inherited['terminals'])
    for issued_path in sorted(root.glob('rounds/**/issued.json')):
        stem=issued_path.parent
        if not (stem/'terminal.json').exists(): raise ExplorationError('unresolved parent issued request')
        terminal=json.loads((stem/'terminal.json').read_text('utf-8'))
        if terminal.get('terminal_identity') != sha256_json({k:v for k,v in terminal.items() if k!='terminal_identity'}):
            raise ExplorationError('parent terminal integrity')
        issued=json.loads(issued_path.read_text('utf-8'))
        request=json.loads((stem/'request.json').read_text('utf-8'))
        if issued['request_identity'] != sha256_json(request) or request!=json.loads((stem/'wire-request.json').read_text('utf-8')):
            raise ExplorationError('parent request/wire integrity')
        total=terminal.get('usage',{}).get('total_tokens') if isinstance(terminal.get('usage'),dict) else None
        if terminal.get('retry_eligible') and terminal.get('execution_state') == 'NOT_STARTED': total=0
        if isinstance(total,bool) or not isinstance(total,int) or total<0: raise ExplorationError('parent usage UNKNOWN')
        calls+=1;tokens+=total
        accounting = stem/'input-accounting.json'
        if not accounting.exists(): accounting = stem.parent.parent/'input-accounting.json'
        reservations+=json.loads(accounting.read_text('utf-8'))['reserved_tokens']
        terminals.append({'root':str(root),'round':stem.name,'total_tokens':total,'finish_reason':terminal['finish_reason']})
    return {'calls':calls,'tokens':tokens,'reservations':reservations,'terminals':terminals}


def short_payload(state, question, budget):
    new=[]
    for event in state['events'][state.get('delivered_until',0):]:
        result=event['result']
        if 'hits' in result:
            # Match batch-mode read identity; never make model/action numbers locators.
            result={'query':result['query'],'hits':[{
                'unit_id':h['unit_id'],
                'title':h['record_context']['record_title'],'section':h['record_context']['section_name'],
                'kind':h['canonical_address']['canonical_unit_kind'],'snippet':h['snippet'][:60],
                'snippet_omitted_chars':h['snippet_omitted_chars']+max(0,len(h['snippet'])-60),
            } for h in result['hits']]}
        elif 'canonical_payload' in result:
            if result.get('error'):
                original = event['result']
                result = {
                    'unit_id': result['unit_id'],
                    'error': result['error'],
                    'message': result.get('message', ''),
                    'requested_max_chars': result.get('requested_max_chars'),
                    'min_required_max_chars': result.get('min_required_max_chars'),
                    'locator': original['source']['canonical_address'],
                    'title': original['source']['record_context']['record_title'],
                    'ru_text': '',
                    'partial': True,
                    'omitted_chars': result.get('omitted_chars', 0),
                }
                for k in ('context_scope', 'omitted_component_unit_count', 'source_binding_status', 'context_unit_ordinals'):
                    if k in original:
                        result[k] = original[k]
                if 'source' in original:
                    if 'content_role' in original['source']:
                        result['source_role'] = original['source']['content_role']
                    if 'provenance' in original['source']:
                        result['source_provenance'] = original['source']['provenance']
            else:
                result={k:result[k] for k in ('unit_id','ru_text','partial','omitted_chars','context_scope','omitted_component_unit_count','source_binding_status')}
                original=event['result']
                result['locator']=original['source']['canonical_address']
                result['title']=original['source']['record_context']['record_title']
                result['projection']='RU text from verified Canonical; duplicate Canonical payload withheld; full partial receipt preserved'
                result['withheld_canonical_payload_chars']=len(original['canonical_payload'])
                result['source_role']=original['source']['content_role']
                result['source_provenance']=original['source']['provenance']
        else: result=model_result(result)
        new.append({'action_index':event['round'],'result':result})
    return {'question':question,'remaining_calls':budget.rounds-state['provider_calls'],
            'remaining_tokens':budget.total_tokens-state['used']['tokens'],
            'remaining_searches':budget.searches-state['used']['searches'],
            'remaining_reads':budget.reads-state['used']['reads'],
            'remaining_pages':budget.pages-len(state['pages']),
            'remaining_canonical_chars':budget.canonical_chars-state['used']['canonical_chars'],
            'memory':state['memory'],'clues':state['pending_clues'],
            'read_sources':[{'unit_id':r['unit_id'],'title':r['source']['record_context']['record_title'],
                            'partial':r['partial']} for r in state['read_sources']],
            'prior_actions':[{'index':e['round'],'action':e['action']} for e in state['events']],
            'new_tool_results':new}


@dataclass(frozen=True)
class E2Budget:
    rounds: int = 6
    searches: int = 8
    reads: int = 12
    pages: int = 8
    canonical_chars: int = 20000
    total_tokens: int = 100000
    output_tokens: int = 8192
    tool_response_chars: int = 100000
    profile: str = "standard"

    def __post_init__(self):
        for k, v in asdict(self).items():
            if k == "profile":
                continue
            if isinstance(v, bool) or not isinstance(v, int) or v <= 0:
                raise ExplorationError("budget must be positive integers")
        if self.profile in ("standard", "default"):
            if (self.rounds > 6 or self.searches > 8 or self.reads > 12 or self.pages > 8
                    or self.canonical_chars > 20000 or self.total_tokens > 100000
                    or self.output_tokens > 8192 or self.tool_response_chars > 100000):
                raise ExplorationError("E2 single-case hard ceiling exceeded")
        elif self.profile == "research":
            if (self.rounds > 50 or self.searches > 100 or self.reads > 200 or self.pages > 100
                    or self.canonical_chars > 500000 or self.total_tokens > 2000000
                    or self.output_tokens > 8192 or self.tool_response_chars > 2000000):
                raise ExplorationError("E2 research budget hard ceiling exceeded")
        else:
            raise ExplorationError(f"unknown budget profile: {self.profile}")

    @classmethod
    def research(
        cls,
        *,
        rounds: int = 20,
        searches: int = 30,
        reads: int = 50,
        pages: int = 30,
        canonical_chars: int = 100000,
        total_tokens: int = 250000,
        output_tokens: int = 8192,
        tool_response_chars: int = 500000,
    ) -> E2Budget:
        return cls(
            rounds=rounds,
            searches=searches,
            reads=reads,
            pages=pages,
            canonical_chars=canonical_chars,
            total_tokens=total_tokens,
            output_tokens=output_tokens,
            tool_response_chars=tool_response_chars,
            profile="research",
        )


@dataclass(frozen=True)
class E2ResearchBudget(E2Budget):
    rounds: int = 20
    searches: int = 30
    reads: int = 50
    pages: int = 30
    canonical_chars: int = 100000
    total_tokens: int = 250000
    output_tokens: int = 8192
    tool_response_chars: int = 500000
    profile: str = "research"


def once(path: Path, value: Any):
    if path.exists():
        raise ExplorationError(f"immutable evidence already exists: {path.name}")
    atomic_write(path, canonical_json_bytes(value))


def _save(root, state):
    _e1_save(root, state)
    path = root / 'checkpoints' / (state['checkpoint_identity'] + '.json')
    if not path.exists(): once(path, state)
    return state


def verify_response_attempts(stem):
    """Verify new nested attempts without changing legacy request/hash rules."""
    paths = sorted((stem/'attempts').glob('*/issued.json'))
    if not paths: return
    aggregate = json.loads((stem/'terminal.json').read_text('utf-8'))
    identities = []
    retry_identities = []
    for idx, path in enumerate(paths, 1):
        issued = json.loads(path.read_text('utf-8'))
        request = json.loads(path.with_name('request.json').read_text('utf-8'))
        wire = json.loads(path.with_name('wire-request.json').read_text('utf-8'))
        terminal = json.loads(path.with_name('terminal.json').read_text('utf-8'))
        if (issued['run_identity'] != aggregate['run_identity'] or request != wire
                or issued['request_identity'] != sha256_json(request)
                or terminal['terminal_identity'] != sha256_json({k:v for k,v in terminal.items() if k != 'terminal_identity'})):
            raise ExplorationError('Responses attempt integrity mismatch')
        descriptor = terminal.get('stream')
        if descriptor:
            stream_path = path.parent/'stream.jsonl.gz'
            if sha256(stream_path.read_bytes()).hexdigest() != descriptor['sha256']:
                raise ExplorationError('Responses stream integrity mismatch')
        raw_descriptor = terminal.get('raw_error')
        if raw_descriptor and sha256(path.with_name('raw-error.bin').read_bytes()).hexdigest() != raw_descriptor['sha256']:
            raise ExplorationError('Responses raw error integrity mismatch')
        retry_path = path.with_name('retry.json')
        if idx < len(paths):
            if not retry_path.exists():
                raise ExplorationError('Responses attempt missing retry evidence')
            retry = json.loads(retry_path.read_text('utf-8'))
            expected_reason = terminal.get('retry_reason', 'rate_limit_or_network')
            if (retry.get('same_model') != aggregate['wire_model']
                    or retry.get('same_route') != aggregate['route']
                    or retry.get('next_attempt') != idx + 1
                    or not isinstance(retry.get('delay_seconds'), (int, float))
                    or retry.get('duplicate_billing_risk', False) != (terminal.get('execution_state') == 'EXECUTION UNKNOWN')
                    or retry.get('retry_reason', 'rate_limit_or_network') != expected_reason):
                raise ExplorationError('Responses retry evidence integrity mismatch')
            if expected_reason == 'format_error' and retry.get('format_error') != terminal.get('format_error'):
                raise ExplorationError('Responses retry format error mismatch')
            if 'retry_identity' in retry:
                if retry['retry_identity'] != sha256_json({k: v for k, v in retry.items() if k != 'retry_identity'}):
                    raise ExplorationError('Responses retry identity mismatch')
                retry_identities.append(retry['retry_identity'])
        else:
            if retry_path.exists():
                raise ExplorationError('Responses unexpected retry evidence on final attempt')
        identities.append(terminal['terminal_identity'])
    if aggregate.get('attempt_identities') != identities or aggregate['calls'] != len(paths):
        raise ExplorationError('Responses aggregate attempt mismatch')
    if 'retry_identities' in aggregate:
        if aggregate['retry_identities'] != retry_identities:
            raise ExplorationError('Responses aggregate retry identity mismatch')
    expected_risk = any(
        json.loads(p.with_name('terminal.json').read_text('utf-8')).get('duplicate_billing_risk', False)
        or (p.with_name('retry.json').exists() and json.loads(p.with_name('retry.json').read_text('utf-8')).get('duplicate_billing_risk', False))
        for p in paths
    )
    if aggregate.get('duplicate_billing_risk', False) != expected_risk:
        raise ExplorationError('Responses aggregate duplicate billing risk mismatch')


def reconcile_provider_accounting(root, state):
    issued = list(root.glob('rounds/**/issued.json'))
    terminals = [json.loads(p.with_name('terminal.json').read_text('utf-8')) for p in issued if p.with_name('terminal.json').exists()]
    inherited=state.get('parent')
    parent=json.loads((root/'parent-checkpoint.json').read_text('utf-8')) if inherited else None
    inherited_account=state.get('inherited_accounting')
    inherited_calls=inherited_account['calls'] if inherited_account else parent['provider_calls'] if parent else 0
    state['provider_calls'] = len(issued)+inherited_calls
    totals = [0 if t.get('retry_eligible') and t.get('execution_state') == 'NOT_STARTED'
              else t.get('usage', {}).get('total_tokens') if isinstance(t.get('usage'), dict) else None for t in terminals]
    known=sum(v for v in totals if isinstance(v,int))+(inherited_account['tokens'] if inherited_account else parent['provider_audit']['known_total_tokens'] if parent else 0)
    state['used']['tokens']=known
    if inherited_account:
        state['model_rounds']=state['provider_calls']
        state['used']['token_reservations']=inherited_account['reservations']+sum(json.loads(p.read_text('utf-8'))['reserved_tokens'] for p in root.glob('rounds/*/input-accounting.json'))

    round_terminals = [json.loads(p.read_text('utf-8')) for p in root.glob('rounds/*/terminal.json')]
    parent_risk = parent.get('duplicate_billing_risk', False) if parent else False
    parent_risk_calls = parent.get('provider_audit', {}).get('duplicate_billing_risk_calls', 0) if parent else 0
    has_billing_risk = parent_risk or any(t.get('duplicate_billing_risk', False) for t in terminals + round_terminals)
    billing_risk_calls = parent_risk_calls + sum(bool(t.get('duplicate_billing_risk')) for t in terminals)
    state['duplicate_billing_risk'] = has_billing_risk

    state['provider_audit'] = {'issued_calls':len(issued)+inherited_calls, 'child_issued_calls':len(issued),
        'terminal_count':len(terminals)+(inherited_account['calls'] if inherited_account else parent['provider_audit']['terminal_count'] if parent else 0),
        'known_total_tokens':known,
        'usage_unknown_calls':sum(not isinstance(v,int) for v in totals) + max(0,len(issued)-len(terminals))+(0 if inherited_account else parent['provider_audit']['usage_unknown_calls'] if parent else 0),
        'actual_cost':'UNKNOWN', 'currency':'UNKNOWN',
        'retry_calls':sum(max(0, len(list(p.glob('attempts/*/issued.json')))-1) for p in (root/'rounds').glob('*')),
        'fallback_calls':0,
        'duplicate_billing_risk': has_billing_risk,
        'duplicate_billing_risk_calls': billing_risk_calls}


def body_chars(receipt):
    if not receipt.get('canonical_payload'):
        return 0
    # Count all string leaves of delivered Canonical unit values; metadata is separate.
    def count(v):
        if isinstance(v, str): return len(v)
        if isinstance(v, dict): return sum(count(x) for x in v.values())
        if isinstance(v, list): return sum(count(x) for x in v)
        return 0
    try:
        return sum(count(u['value']) for u in json.loads(receipt['canonical_payload'])['units'])
    except (ValueError, KeyError, TypeError):
        # A visible truncated JSON prefix cannot be interpreted: charge its entire prefix.
        return len(receipt['canonical_payload'])


def model_result(result):
    if 'hits' in result and isinstance(result.get('hits'), list):
        hits=[]
        for index,h in enumerate(result['hits']):
            address=h['canonical_address']; context=h['record_context']
            hits.append({'index':index,'unit_id':h['unit_id'],
                'locator':address['source_identity_key']+':s'+str(address['section_ordinal'])+':u'+str(address['canonical_unit_ordinal']),
                'kind':address['canonical_unit_kind'],'title':context['record_title'],
                'section':context['section_name'],'snippet':h['snippet'][:80],
                'snippet_omitted_chars':h['snippet_omitted_chars']+max(0,len(h['snippet'])-80)})
        return {'mode':result['mode'],'query':result['query'],'hits':hits,
                'projection':'compact_search_locator_and_80_char_snippet_full_receipt_persisted',
                'omitted_hit_count':result.get('omitted_hit_count',0)}
    if 'canonical_payload' not in result:
        return result
    if result.get('error'):
        res = {
            'unit_id': result['unit_id'],
            'error': result['error'],
            'message': result.get('message', ''),
            'requested_max_chars': result.get('requested_max_chars'),
            'min_required_max_chars': result.get('min_required_max_chars'),
            'ru_text': '',
            'partial': True,
            'omitted_chars': result.get('omitted_chars', 0),
        }
        if 'source' in result:
            res['canonical_address'] = result['source']['canonical_address']
            res['record_context'] = result['source']['record_context']
            if 'content_role' in result['source']:
                res['source_role'] = result['source']['content_role']
            if 'provenance' in result['source']:
                res['source_provenance'] = result['source']['provenance']
        if 'ru_text_sha256' in result:
            res['ru_text_sha256'] = result['ru_text_sha256']
        if 'canonical_record_sha256' in result:
            res['canonical_record_sha256'] = result['canonical_record_sha256']
        if 'canonical_payload_sha256' in result:
            res['canonical_payload_sha256'] = result['canonical_payload_sha256']
        if 'context_scope' in result:
            res['context_scope'] = result['context_scope']
        if 'omitted_component_unit_count' in result:
            res['omitted_component_unit_count'] = result['omitted_component_unit_count']
        if 'source_binding_status' in result:
            res['source_binding_status'] = result['source_binding_status']
        if 'context_unit_ordinals' in result:
            res['context_unit_ordinals'] = result['context_unit_ordinals']
        return res
    try:
        payload = json.loads(result['canonical_payload'])
        values = [{k: u[k] for k in ('ordinal', 'kind', 'value', 'content_role', 'provenance')} for u in payload['units']]
    except (ValueError, KeyError, TypeError):
        values = {'truncated_canonical_prefix': result['canonical_payload']}
    return {k: result[k] for k in ('unit_id', 'ru_text', 'partial', 'omitted_chars', 'context_scope', 'omitted_component_unit_count', 'ru_text_sha256', 'canonical_record_sha256')} | {
        'canonical_address': result['source']['canonical_address'],
        'record_context': result['source']['record_context'], 'canonical_units': values,
    }


def _validate_e2_action(action):
    """Validate bounded E2 argument shapes before claiming action protocol success."""
    _validate_action(action)
    def integer(value, low, high):
        return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high
    kind = action['action']
    if kind == 'search':
        valid = (isinstance(action.get('query'), str) and 1 <= len(action['query'].strip()) <= 160
                 and integer(action.get('top_k', 5), 1, 5))
    elif kind == 'read':
        reference = action.get('unit_id')
        hit = action.get('hit')
        valid = ((isinstance(reference, str) and bool(reference)) if 'unit_id' in action else
            isinstance(hit, dict) and set(hit) == {'round', 'index'}
            and all(integer(v, 0, 100000) for v in hit.values()))
        valid = valid and integer(action.get('max_chars', 20000), 1, 2_000_000) and integer(action.get('neighbors', 0), 0, 1)
    elif kind == 'clue':
        valid = (isinstance(action.get('text'), str) and 1 <= len(action['text']) <= 1000
            and isinstance(action.get('source_unit_ids'), list) and bool(action['source_unit_ids'])
            and all(isinstance(uid, str) and bool(uid) for uid in action['source_unit_ids']))
    else:
        valid = isinstance(action.get('reason'), str) and 1 <= len(action['reason']) <= 1000
    if not valid:
        raise ExplorationError('invalid bounded E2 action arguments')


def _diagnose_decision_format(visible_text: str, mode: str = "batch") -> dict[str, Any]:
    """Diagnose why visible_text is not a valid decision, returning detailed error diagnostics."""
    if not isinstance(visible_text, str) or not visible_text.strip():
        return {
            "valid": False,
            "error_type": "EmptyContentError",
            "message": "Model returned empty visible text.",
        }
    try:
        dec = json.loads(visible_text)
    except Exception as exc:
        return {
            "valid": False,
            "error_type": "JSONDecodeError",
            "message": f"Output is not valid JSON: {str(exc)[:200]}",
            "raw_prefix": visible_text[:300],
        }
    if not isinstance(dec, dict):
        return {
            "valid": False,
            "error_type": "InvalidJSONStructure",
            "message": f"Expected JSON object (dict), but received {type(dec).__name__}.",
        }
    if mode == "single_action":
        if "action" not in dec:
            return {
                "valid": False,
                "error_type": "MissingActionField",
                "message": "Decision object missing required 'action' field.",
            }
        act_kind = dec.get("action")
        if act_kind not in {"search", "read", "clue", "stop"}:
            return {
                "valid": False,
                "error_type": "InvalidActionType",
                "message": f"Action '{act_kind}' is not one of ('search', 'read', 'clue', 'stop').",
            }
        try:
            _validate_e2_action(dec)
        except Exception as exc:
            return {
                "valid": False,
                "error_type": "InvalidActionArguments",
                "message": str(exc),
            }
        if act_kind in {"clue", "stop"}:
            text_or_reason = dec.get("text") if act_kind == "clue" else dec.get("reason")
            if not isinstance(text_or_reason, str) or len(text_or_reason) > 500:
                return {
                    "valid": False,
                    "error_type": "TextBoundExceeded",
                    "message": "Text or reason exceeds 500 characters or is not a string.",
                }
        return {"valid": True, "error_type": None, "message": None}
    elif mode == "batch":
        expected_keys = {"actions", "memory", "rationale"}
        if set(dec) != expected_keys:
            missing = expected_keys - set(dec)
            extra = set(dec) - expected_keys
            return {
                "valid": False,
                "error_type": "SchemaFieldMismatch",
                "message": f"Expected top-level keys {sorted(expected_keys)}. Missing: {sorted(missing)}. Extra: {sorted(extra)}.",
            }
        acts = dec.get("actions")
        if not isinstance(acts, list) or not (1 <= len(acts) <= 6):
            return {
                "valid": False,
                "error_type": "ActionBatchLimitError",
                "message": f"'actions' must be a list of 1 to 6 actions, but got {type(acts).__name__} with length {len(acts) if isinstance(acts, list) else 0}.",
            }
        for idx, a in enumerate(acts):
            if not isinstance(a, dict):
                return {
                    "valid": False,
                    "error_type": "InvalidActionStructure",
                    "message": f"Action at index {idx} must be a dict, got {type(a).__name__}.",
                }
            try:
                _validate_e2_action(a)
            except Exception as exc:
                return {
                    "valid": False,
                    "error_type": "InvalidActionArguments",
                    "message": f"Action at index {idx} ('{a.get('action')}') invalid: {str(exc)}",
                }
        mem = dec.get("memory")
        if not isinstance(mem, dict) or set(mem) != {"status", "text"}:
            return {
                "valid": False,
                "error_type": "InvalidMemoryStructure",
                "message": "Field 'memory' must be a dict with exactly keys {'status', 'text'}.",
            }
        if mem.get("status") != HYPOTHESIS:
            return {
                "valid": False,
                "error_type": "InvalidMemoryStatus",
                "message": f"Memory status must be '{HYPOTHESIS}', got '{mem.get('status')}'.",
            }
        if not isinstance(mem.get("text"), str) or len(mem["text"]) > 1600:
            return {
                "valid": False,
                "error_type": "MemoryTextBoundError",
                "message": f"Memory text must be a string <= 1600 characters, got {type(mem.get('text')).__name__}.",
            }
        rat = dec.get("rationale")
        if not isinstance(rat, str) or len(rat) > 1500:
            return {
                "valid": False,
                "error_type": "RationaleBoundError",
                "message": f"Rationale must be a string <= 1500 characters, got {type(rat).__name__}.",
            }
        return {"valid": True, "error_type": None, "message": None}
    return {
        "valid": False,
        "error_type": "UnknownDecisionMode",
        "message": f"Unknown decision mode '{mode}'.",
    }


def _is_valid_decision_content(visible_text: str, mode: str = "batch") -> bool:
    return _diagnose_decision_format(visible_text, mode=mode)["valid"]


def _build_format_correction_feedback(diagnosis: Mapping[str, Any], mode: str = "batch") -> dict[str, Any]:
    """Construct a structured feedback payload instructing the model to correct its output format."""
    required_schema = (
        "Top-level JSON object with exactly: "
        "'actions' (array of 1..6 action objects), "
        "'memory' ({'status': 'unverified_hypothesis_not_fact', 'text': string <= 1600 chars}), "
        "'rationale' (string <= 1500 chars)."
    ) if mode == "batch" else (
        "Top-level JSON object with: "
        "'action' ('search'|'read'|'clue'|'stop') and required arguments."
    )
    return {
        "error": "model_output_format_error",
        "error_type": diagnosis.get("error_type", "SchemaValidationError"),
        "message": diagnosis.get("message", "Model output failed schema validation"),
        "mode": mode,
        "required_schema": required_schema,
        "instruction": (
            "Your previous response failed schema validation. Re-emit a valid decision adhering "
            "strictly to the schema above. Output raw JSON ONLY. Do NOT wrap in markdown code blocks."
        ),
    }


def _inject_format_feedback(body: Mapping[str, Any], feedback: Mapping[str, Any]) -> dict[str, Any]:
    """Inject format correction feedback into the request body's input parameter."""
    new_body = dict(body)
    raw_input = body.get("input", "")
    try:
        payload = json.loads(raw_input)
        if isinstance(payload, dict):
            payload["format_correction"] = feedback
            new_body["input"] = canonical_json_bytes(payload).decode("utf-8")
            return new_body
    except Exception:
        pass
    new_body["input"] = raw_input + "\n\n[FORMAT_CORRECTION_FEEDBACK]: " + canonical_json_bytes(feedback).decode("utf-8")
    return new_body


def _exception_diagnostics(exc, observation, *, stream_events, response_terminal):
    """Bounded exception graph, never exception text/args/requests/tracebacks.

    Both explicit causes and implicit contexts are retained; suppressed contexts
    are labelled, not promoted to causes. Only known exception classes and
    numeric OS/TLS codes cross the audit boundary.
    """
    import builtins
    import socket
    import ssl
    import httpcore
    import httpx
    import openai

    known = {value: value.__module__ + '.' + value.__name__
             for module in (builtins, socket, ssl, httpcore, httpx, openai)
             for value in tuple(vars(module).values())
             if type(value) is type and issubclass(value, BaseException)}
    known[ExplorationError] = ExplorationError.__module__ + '.ExplorationError'
    pending, nodes = [exc], []
    indices = {id(exc): 0}
    truncated = False
    for item in pending:
        node = {'type': known.get(type(item), 'UNRECOGNIZED_EXCEPTION'),
                'context_suppressed': BaseException.__suppress_context__.__get__(item), 'codes': {}}
        if isinstance(item, OSError):
            for name in ('errno', 'winerror', 'verify_code'):
                # Numeric primitives only; do not stringify arbitrary code fields.
                try:
                    value = getattr(item, name, None)
                except Exception:
                    value = None
                if type(value) is int and -(2**31) <= value < 2**31:
                    node['codes'][name] = value
        for name in ('cause', 'context'):
            linked = getattr(BaseException, '__' + name + '__').__get__(item)
            if linked is None:
                node[name] = None
            elif id(linked) in indices:
                node[name] = indices[id(linked)]
            elif len(pending) < 16:
                node[name] = indices[id(linked)] = len(pending)
                pending.append(linked)
            else:
                node[name] = 'OMITTED_LIMIT'
                truncated = True
        nodes.append(node)
    return {'schema': 'e2-exception-diagnostics-v1', 'exceptions': nodes,
            'truncated': truncated, 'text_policy': 'OMITTED',
            'observations': {
                'request_hook_observed': observation['calls'] > 0,
                'http_response_hook_observed': observation['http_status'] is not None,
                'stream_events_observed': stream_events,
                'responses_terminal_observed': response_terminal,
                'request_hook_proves_server_receipt': False,
                'failure_phase': 'UNKNOWN',
            }}


class E2SDK:
    """Existing SDK route/helpers, with an E2 decision protocol instead of atomic extraction."""
    def __init__(self, route=None, *, model=None, transport=None, environment=None,
                 max_attempts=None, before_dispatch=None, mode=None, budget_profile=None,
                 format_retry_enabled=None):
        import openai
        env = dict(os.environ if environment is None else environment)
        self.profile = e2_route(model, route, env, offline=transport is not None)

        if mode is None:
            mode = 'research' if budget_profile == 'research' else 'standard'
        if mode not in ('standard', 'research'):
            raise ExplorationError("mode must be 'standard' or 'research'")
        self.mode = mode
        if format_retry_enabled is None:
            format_retry_enabled = (mode == 'research')
        self.format_retry_enabled = format_retry_enabled

        if mode == 'research':
            effective_attempts = 5 if max_attempts is None else max_attempts
            if isinstance(effective_attempts, bool) or not isinstance(effective_attempts, int) or not 1 <= effective_attempts <= 5:
                raise ExplorationError('attempt ceiling must be 1..5')
            retry_policy = 'research_broadened_network_provider_retry'
        else:
            effective_attempts = 3 if max_attempts is None else max_attempts
            if isinstance(effective_attempts, bool) or not isinstance(effective_attempts, int) or not 1 <= effective_attempts <= 3:
                raise ExplorationError('attempt ceiling must be 1..3')
            retry_policy = 'exact_tokenmetro_pre_generation_tpm_429_only'

        self.max_attempts = effective_attempts
        self.env = env
        self.transport = transport
        self.before_dispatch = before_dispatch
        self.config = {'adapter': 'p05-navi-e2-sdk-responses-0.2', 'route': self.profile.safe_dict(),
                       'sdk_version': openai.__version__, 'max_retries': 0, 'fallback': False,
                       'stream': True, 'include_usage': False, 'timeout_seconds': 180,
                       'max_attempts': effective_attempts, 'retry_policy': retry_policy,
                       'mode': mode, 'format_retry_enabled': format_retry_enabled}
        self._config_identity = sha256_json(self.config)

    def __call__(self, stem, body, run_identity, *, decision_mode=None, attempt_limit=None):
        """Persist every attempt. An interrupted wrapper is never automatically resumed."""
        if (sha256_json(self.config) != self._config_identity
                or self.config['route'] != self.profile.safe_dict() or self.max_attempts != self.config['max_attempts']
                or self.mode != self.config.get('mode') or self.format_retry_enabled != self.config.get('format_retry_enabled')):
            raise ExplorationError('immutable provider configuration changed')
        stem = Path(stem)
        limit = self.max_attempts if attempt_limit is None else min(self.max_attempts, attempt_limit)
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ExplorationError('attempt budget exhausted')
        if body.get('model') != self.profile.model_id or body.get('stream') is not True:
            raise ExplorationError('request model/stream differs from immutable route')
        if 'messages' in body or 'max_tokens' in body or 'stream_options' in body:
            raise ExplorationError('new E2 execution requires native Responses request')
        if set(body) - {'model', 'instructions', 'input', 'stream', 'max_output_tokens'}:
            raise ExplorationError('unverified Responses parameter')
        if any(value in canonical_json_bytes(body).decode('utf-8')
               for name, value in self.env.items() if name.endswith('_API_KEY') and value):
            raise ExplorationError('credential in request body')
        output = body.get('max_output_tokens')
        if isinstance(output, bool) or not isinstance(output, int) or not 1 <= output <= 8192:
            raise ExplorationError('invalid output ceiling')
        if stem.exists() and any(stem.iterdir()):
            # Runner may have prepared the input reservation before dispatch.
            if set(p.name for p in stem.iterdir()) != {'input-accounting.json'}:
                raise ExplorationError('existing attempt evidence; no automatic reissue')
        once(stem / 'request.json', body)
        current_body = dict(body)
        attempts = []
        prior_execution_unknown = False
        for number in range(1, limit + 1):
            attempt = self._attempt(stem / 'attempts' / f'{number:02d}', current_body, run_identity,
                                    decision_mode=decision_mode,
                                    duplicate_billing_risk=prior_execution_unknown)
            attempts.append(attempt)
            if not attempt['retry_eligible'] or number == limit:
                break

            is_uncertain = (attempt.get('execution_state') == 'EXECUTION UNKNOWN')
            prior_execution_unknown = prior_execution_unknown or is_uncertain

            retry_reason = attempt.get('retry_reason', 'rate_limit_or_network')
            retry_data = {
                'same_model': self.profile.model_id,
                'same_route': self.profile.route,
                'next_attempt': number + 1,
                'duplicate_billing_risk': is_uncertain,
            }
            if retry_reason == 'format_error':
                feedback = _build_format_correction_feedback(attempt.get('format_error', {}), mode=decision_mode)
                current_body = _inject_format_feedback(body, feedback)
                delay = 0
                retry_data.update(
                    delay_seconds=delay,
                    retry_reason='format_error',
                    format_error=attempt.get('format_error'),
                )
            else:
                delay = _retry_after(attempt['headers'], 30) or min(2 ** (number - 1), 30)
                retry_data.update(
                    delay_seconds=delay,
                    retry_reason=retry_reason,
                )
            retry_data['retry_identity'] = sha256_json(retry_data)
            once(stem / 'attempts' / f'{number:02d}' / 'retry.json', retry_data)
            if delay:
                time.sleep(delay)

        terminal = dict(attempts[-1])
        terminal.pop('terminal_identity', None)
        has_risk = any(t.get('duplicate_billing_risk', False) for t in attempts) or prior_execution_unknown
        update_fields = dict(
            calls=sum(t['calls'] for t in attempts),
            retry_calls=len(attempts)-1,
            attempt_identities=[t['terminal_identity'] for t in attempts],
            duplicate_billing_risk=has_risk,
        )
        if len(attempts) > 1:
            update_fields['retry_identities'] = [
                json.loads((stem / 'attempts' / f'{n:02d}' / 'retry.json').read_text('utf-8'))['retry_identity']
                for n in range(1, len(attempts))
            ]
        terminal.update(update_fields)
        terminal['terminal_identity'] = sha256_json(terminal)
        once(stem / 'terminal.json', terminal)
        return terminal

    def _attempt(self, stem, body, run_identity, *, decision_mode=None, duplicate_billing_risk=False):
        import httpx
        import openai
        key = self.env[self.profile.api_key_env]
        stem.mkdir(parents=True, exist_ok=True)
        once(stem / 'request.json', body)
        artifact_root = stem.parents[3]
        writer = _JsonlArtifactWriter(stem / 'stream.jsonl', artifact_root)
        observation = {'calls': 0, 'http_status': None, 'headers': {}}
        tracker = None
        started = time.monotonic()
        content, reasoning, usage, finish = [], [], None, None
        decision_text = _ResponsesDecisionText()
        response_terminal = False
        response_status, terminal_event, response_id = None, None, None
        returned_models = set()
        reasoning_kinds = set()
        terminal_count = 0
        secrets = tuple(v for k, v in self.env.items() if k.endswith('_API_KEY') and v)
        def sensitive(value):
            return any(secret in value for secret in secrets)
        def redact(value):
            for secret in secrets:
                value = value.replace(secret, '[REDACTED]')
            return value
        def request_hook(req):
            if self.before_dispatch is not None: self.before_dispatch()
            observation['calls'] += 1
            if (observation['calls'] != 1 or json.loads(req.content) != body
                    or sensitive(req.content.decode()) or str(req.url) != self.profile.base_url + '/responses'):
                raise ExplorationError('wire identity/accounting mismatch')
            once(stem / 'issued.json', {'run_identity': run_identity, 'request_identity': sha256_json(body),
                                      'issued_at': datetime.now(timezone.utc).isoformat()})
            once(stem / 'wire-request.json', json.loads(req.content))
            once(stem / 'request-metadata.json', {'url': str(req.url), 'method': req.method,
                  'headers': {n: req.headers[n] for n in ('content-type', 'accept') if n in req.headers}})
        def response_hook(res):
            nonlocal tracker
            observation.update(http_status=res.status_code, headers={n: redact(res.headers[n]) for n in ('retry-after', 'x-request-id', 'content-type') if n in res.headers})
            if res.status_code == 200:
                tracker = _DoneTrackingStream(res.stream)
                if res.is_stream_consumed: tracker.observe(res.content)
                res.stream = tracker
            else:
                raw = res.read()
                if sensitive(raw.decode('utf-8', errors='replace')): raise ExplorationError('credential echo')
                atomic_write(stem / 'raw-error.bin', raw)
        error = None
        try:
            with httpx.Client(transport=self.transport, event_hooks={'request': [request_hook], 'response': [response_hook]}) as http_client:
                with openai.OpenAI(base_url=self.profile.base_url, api_key=key, max_retries=0, timeout=180, http_client=http_client) as client:
                    for chunk in client.responses.create(**body):
                        if time.monotonic() - started > self.config['timeout_seconds']:
                            raise ExplorationError('Responses wall-clock deadline; execution indeterminate')
                        data = _chunk_mapping(chunk)
                        if sensitive(json.dumps(data)): raise ExplorationError('credential echo')
                        writer.append(data)
                        decision_text.observe(data)
                        resp = data.get('response') if isinstance(data.get('response'), Mapping) else {}
                        if isinstance(resp.get('model'), str): returned_models.add(resp['model'])
                        if data.get('type') in {'response.reasoning_text.delta', 'response.reasoning_summary_text.delta'}:
                            reasoning_kinds.add(data['type'])
                        if response_terminal:
                            error = {'type': 'ResponsesDecisionTextError', 'message': 'event after Responses terminal'}
                        if data.get("type") == "error":
                            err_obj = data.get("error") if isinstance(data.get("error"), Mapping) else data
                            err_msg = str(err_obj.get("message", "Responses stream error")) if isinstance(err_obj, Mapping) else str(err_obj)
                            error = {'type': 'ResponsesStreamError', 'message': err_msg.replace(key, '[REDACTED]')[:1500]}
                        elif data.get("type") == "response.failed" and error is None:
                            resp = data.get("response") if isinstance(data.get("response"), Mapping) else {}
                            err_obj = resp.get("error") or data.get("error") or "Responses stream failed"
                            err_msg = str(err_obj.get("message", err_obj)) if isinstance(err_obj, Mapping) else str(err_obj)
                            error = {'type': 'ResponsesFailedError', 'message': err_msg.replace(key, '[REDACTED]')[:1500]}
                        value, thought, event_finish, event_usage, event_id, is_term = _responses_event_parts(data)
                        if value: content.append(value)
                        if thought: reasoning.append(thought)
                        if event_finish is not None: finish = event_finish
                        # Provisional usage from created/in-progress events cannot
                        # stand in for missing terminal accounting.
                        if is_term: usage = event_usage
                        if is_term:
                            response_terminal = True
                            terminal_count += 1
                            terminal_event = data.get('type')
                            response_status = resp.get('status')
                            response_id = event_id
                            if not reasoning:
                                summaries = [part['text'] for item in (resp.get('output') or [])
                                    if isinstance(item, Mapping) and item.get('type') == 'reasoning'
                                    for part in (item.get('summary') or [])
                                    if isinstance(part, Mapping) and part.get('type') == 'summary_text' and isinstance(part.get('text'), str)]
                                reasoning.extend(summaries)
                                if summaries: reasoning_kinds.add('response.reasoning_summary_text.terminal')
        except Exception as exc:
            if isinstance(exc, (openai.APIConnectionError, httpx.TransportError, OSError)):
                diagnostics = _exception_diagnostics(exc, observation,
                    stream_events=writer.chunk_count, response_terminal=response_terminal)
                # Transport text can embed URLs, credentials or request bodies;
                # static summaries keep the existing error interface safe.
                message = ('Request timed out.' if isinstance(exc, openai.APITimeoutError)
                           else 'Connection error.' if isinstance(exc, openai.APIConnectionError)
                           else 'Transport exception text omitted; see diagnostics.')
                error = {'type': diagnostics['exceptions'][0]['type'].rsplit('.', 1)[-1],
                         'message': message, 'diagnostics': diagnostics}
            else:
                error = {'type': type(exc).__name__, 'message': redact(str(exc))[:1500]}
        if isinstance(usage, Mapping):
            usage = dict(usage)
            inp = usage.get("input_tokens", usage.get("prompt_tokens"))
            out = usage.get("output_tokens", usage.get("completion_tokens"))
            if "total_tokens" not in usage and isinstance(inp, int) and isinstance(out, int) and not isinstance(inp, bool) and not isinstance(out, bool):
                usage["total_tokens"] = inp + out
        has_reliable_usage = (isinstance(usage, Mapping)
            and all(isinstance(usage.get(k), int) and not isinstance(usage[k], bool) and usage[k] >= 0
                    for k in ('input_tokens', 'output_tokens', 'total_tokens'))
            and usage['total_tokens'] == usage['input_tokens'] + usage['output_tokens'])
        if has_reliable_usage:
            details = usage.get('output_tokens_details') or {}
            reasoning_tokens = details.get('reasoning_tokens')
            if reasoning_tokens is not None:
                has_reliable_usage = (isinstance(reasoning_tokens, int) and not isinstance(reasoning_tokens, bool)
                                      and 0 <= reasoning_tokens <= usage['output_tokens'])
        visible_text = ''.join(content)
        if response_terminal and error is None:
            try:
                visible_text = decision_text.text()
            except ValueError as exc:
                error = {'type': 'ResponsesDecisionTextError', 'message': str(exc)}
        stream_complete = bool(finish is not None and response_terminal and error is None)
        model_verified = returned_models == {self.profile.model_id}
        protocol_success = bool(stream_complete and terminal_count == 1 and finish == 'stop'
                                and response_status == 'completed' and model_verified and has_reliable_usage)
        complete = bool(protocol_success and visible_text.strip())
        if decision_mode is None:
            instructions = body.get("instructions", "")
            single_action_prompt = canonical_json_bytes(SINGLE_ACTION_PROMPT).decode("utf-8")
            research_single_action_prompt = canonical_json_bytes(RESEARCH_SINGLE_ACTION_PROMPT).decode("utf-8")
            if instructions in (single_action_prompt, research_single_action_prompt):
                decision_mode = "single_action"
            else:
                decision_mode = "batch"
        decision_diagnosis = _diagnose_decision_format(visible_text, mode=decision_mode) if complete else {
            "valid": False, "error_type": "IncompleteGeneration", "message": "Stream incomplete or errored."
        }
        decision_valid = decision_diagnosis["valid"]
        decision_complete = bool(complete and decision_valid)
        terminal = observation | {'run_identity': run_identity, 'finish_reason': finish, 'complete': complete,
            'stream_complete': stream_complete, 'decision_complete': decision_complete,
            'content': visible_text, 'reasoning': ''.join(reasoning), 'usage': usage or 'UNKNOWN',
            'decision_message': decision_text.selection,
            'usage_normalized': _normalize_usage(usage), 'error': error,
            'latency_ms': round((time.monotonic() - started)*1000, 3), 'stream': writer.descriptor(),
            'actual_cost': 'UNKNOWN', 'currency': 'UNKNOWN'}
        terminal.update(logical_model=self.profile.logical_model, route=self.profile.route,
            wire_model=self.profile.model_id, returned_models=sorted(returned_models), model_verified=model_verified,
            response_id=response_id, terminal_event=terminal_event, response_status=response_status,
            responses_protocol_success=protocol_success, reasoning_event_types=sorted(reasoning_kinds),
            reasoning_availability='provided' if reasoning else 'NOT_PROVIDED',
            execution_state='RESOLVED' if response_terminal else 'EXECUTION UNKNOWN',
            retry_eligible=False)
        raw = (stem / 'raw-error.bin').read_bytes() if (stem / 'raw-error.bin').exists() else None
        retry_probe = dict(route='tokenmetro' if self.profile.route == 'tokenmetro_qwen' else self.profile.route,
            http_status=observation['http_status'], sdk_error_type=error['type'] if error else None,
            stream_chunk_count=writer.chunk_count, stream_complete=stream_complete,
            visible_chars=len(visible_text), reasoning_chars=len(terminal['reasoning']), usage=usage,
            finish_reason=finish)

        if self.config.get('retry_policy') == 'research_broadened_network_provider_retry':
            eligible, state_override = research_retry_classifier(retry_probe, raw, error=error)
            terminal['retry_eligible'] = eligible
            terminal['execution_state'] = state_override
        else:
            terminal['retry_eligible'] = safe_rate_limit_retry(retry_probe, raw)
            if terminal['retry_eligible']:
                terminal['execution_state'] = 'NOT_STARTED'
            elif observation['http_status'] in {400, 401, 403, 404, 422} and not writer.chunk_count:
                terminal['execution_state'] = 'REJECTED'
            else:
                terminal['execution_state'] = terminal['execution_state']

        if not terminal['retry_eligible'] and complete and not decision_valid and self.format_retry_enabled and self.max_attempts > 1:
            terminal['retry_eligible'] = True
            terminal['retry_reason'] = 'format_error'
            terminal['format_error'] = decision_diagnosis
            terminal['execution_state'] = 'RESOLVED'

        terminal['duplicate_billing_risk'] = bool(duplicate_billing_risk)
        terminal['raw_error'] = {'sha256': sha256(raw).hexdigest(), 'byte_count': len(raw)} if raw is not None else None
        terminal['terminal_identity'] = sha256_json(terminal)
        once(stem / 'terminal.json', terminal)
        return terminal


def execute_action(tools, state, action, budget):
    _validate_action(action)
    kind = action['action']
    used = state['used']
    if kind == 'search':
        if used['searches'] >= budget.searches: raise ExplorationBudgetExceeded('search limit')
        k = action.get('top_k', 5)
        if isinstance(k, bool) or not isinstance(k, int) or not 1 <= k <= 5: raise ExplorationError('top_k limit')
        result = tools.search(action['query'], top_k=k)
        used['searches'] += 1
        state['available_ids'] = sorted(set(state['available_ids']) | {h['unit_id'] for h in result['hits']})
    elif kind == 'read':
        if used['reads'] >= budget.reads: raise ExplorationBudgetExceeded('read limit')
        result = None
        try:
            uid = _resolve_read(action, state['events'])
        except ExplorationError as exc:
            if budget.profile == 'research':
                result = {
                    'error': 'unauthorized_or_invalid_unit_id',
                    'message': str(exc),
                    'requested_action': dict(action),
                }
            else:
                raise

        if result is None:
            if uid not in state['available_ids']:
                if budget.profile == 'research':
                    result = {
                        'error': 'unauthorized_or_invalid_unit_id',
                        'message': 'read identity was not provided by seed/search',
                        'requested_unit_id': uid,
                    }
                else:
                    raise ExplorationError('read identity was not provided by seed/search')

        if result is None:
            requested = action.get('max_chars', 20000)
            neighbors = action.get('neighbors', 0)
            if isinstance(neighbors, bool) or neighbors not in (0, 1): raise ExplorationError('neighbor limit')
            max_read_ceiling = 2_000_000 if budget.profile == 'research' else 20000
            if isinstance(requested, bool) or not isinstance(requested, int) or not 1 <= requested <= max_read_ceiling: raise ExplorationError('read limit')
            if used['canonical_chars'] >= budget.canonical_chars: raise ExplorationBudgetExceeded('Canonical body limit')
            try:
                loaded = tools._load_selected_ru([uid])
            except ExplorationError as exc:
                if str(exc) == "scoped RU identities absent from verified snapshot":
                    loaded = {}
                else:
                    raise
            if uid not in loaded:
                if budget.profile == 'research':
                    result = {
                        'error': 'unauthorized_or_invalid_unit_id',
                        'message': 'unknown Retrieval Unit in corpus',
                        'requested_unit_id': uid,
                    }
                else:
                    raise ExplorationError('unknown Retrieval Unit')
            else:
                address = loaded[uid]['source']['canonical_address']

        if result is None:
            if address['record_id'] not in state['pages'] and len(state['pages']) >= budget.pages: raise ExplorationBudgetExceeded('page limit')
            import inspect
            read_params = inspect.signature(tools.read).parameters
            read_kwargs = {
                'max_chars': requested,
                'neighbors': neighbors,
                'fail_on_metadata_budget': False,
            }
            if 'fail_on_unknown_ru' in read_params:
                read_kwargs['fail_on_unknown_ru'] = (budget.profile != 'research')
            try:
                result = tools.read(uid, **read_kwargs)
            except ExplorationBudgetExceeded as exc:
                if str(exc) == "read metadata cannot fit context budget":
                    min_chars = getattr(exc, "min_required_chars", None)
                    base = getattr(exc, "result", None)
                    if base is None:
                        raw_ru = tools._load_selected_ru([uid])[uid]
                        base = {
                            "unit_id": uid, "source": raw_ru["source"],
                            "nested_selector": raw_ru["nested_selector"],
                            "fragment_selector": raw_ru["fragment_selector"],
                            "ru_text": "", "canonical_payload": "",
                            "partial": True,
                        }
                    result = dict(base)
                    result["error"] = "read_budget_too_small"
                    result["message"] = "read metadata cannot fit context budget"
                    result["requested_max_chars"] = requested
                    result["min_required_max_chars"] = min_chars or response_chars(result)
                else:
                    raise
            except ExplorationError as exc:
                if budget.profile == 'research' and str(exc) in ("unknown Retrieval Unit", "scoped RU identities absent from verified snapshot"):
                    result = {
                        'error': 'unauthorized_or_invalid_unit_id',
                        'message': 'unknown Retrieval Unit',
                        'requested_unit_id': uid,
                    }
                else:
                    raise

        if not result.get('error'):
            page = result['source']['canonical_address']['record_id']
            if page not in state['pages'] and len(state['pages']) >= budget.pages: raise ExplorationBudgetExceeded('page limit')
            chars = body_chars(result)
            if used['canonical_chars'] + chars > budget.canonical_chars: raise ExplorationBudgetExceeded('Canonical body limit')
            used['reads'] += 1; used['canonical_chars'] += chars
            state['pages'] = sorted(set(state['pages']) | {page})
            state['read_sources'].append(result)
        else:
            used['reads'] += 1
    elif kind == 'clue':
        text, refs = action.get('text'), action.get('source_unit_ids', [])
        if not isinstance(text, str) or not 1 <= len(text) <= 1000 or not isinstance(refs, list) or not set(refs) <= {r['unit_id'] for r in state['read_sources']}:
            raise ExplorationError('invalid clue or unread references')
        result = {'text': text, 'source_unit_ids': refs, 'status': HYPOTHESIS}
        state['pending_clues'].append(result)
    else:
        reason = action.get('reason')
        if not isinstance(reason, str) or not 1 <= len(reason) <= 1000: raise ExplorationError('stop reason')
        result = {'reason': reason}; state['status'] = 'stopped'
    chars = response_chars(result)
    if used['tool_response_chars'] + chars > budget.tool_response_chars: raise ExplorationBudgetExceeded('tool response limit')
    used['tool_response_chars'] += chars
    state['events'].append({'round': len(state['events']), 'action': dict(action), 'result': result, 'delivered_chars': chars})
    return result


def run_exploration(tools, *, start_ru, output_root, provider, question=QUESTION, budget=None, parent_root=None, single_action_continuation=False):
    budget = budget or E2Budget()
    provider_mode = getattr(provider, 'mode', None)
    if provider_mode is not None:
        expected_mode = 'research' if budget.profile == 'research' else 'standard'
        if provider_mode != expected_mode:
            raise ExplorationError(f"provider mode '{provider_mode}' does not match budget profile '{budget.profile}'")
    if single_action_continuation and parent_root is None: raise ExplorationError('single-action continuation requires frozen parent')
    root = Path(output_root).resolve()
    if any(root.is_relative_to(p.parent.parent) for p in (tools.canonical_path, tools.ru_path, tools.lexical_path)):
        raise ExplorationError('output within immutable input')
    tools.verify_snapshot()
    if budget.profile == "research":
        selected_prompt = RESEARCH_SINGLE_ACTION_PROMPT if single_action_continuation else RESEARCH_PROMPT
    else:
        selected_prompt = SINGLE_ACTION_PROMPT if single_action_continuation else PROMPT
    config = {'runner': 'p05-navi-e2-0.1', 'question': question, 'start_ru': start_ru,
              'tools_identity': tools.identity, 'ru_build_identity': tools.ru_identity,
              'inputs': {k: str(getattr(tools, k)) for k in ('canonical_path', 'ru_path', 'lexical_path')},
              'prompt': selected_prompt, 'single_action_continuation':single_action_continuation, 'budget': asdict(budget), 'provider': provider.config}
    if not single_action_continuation: config.pop('single_action_continuation')
    parent = None
    inherited=None
    if parent_root is not None:
        parent_root=Path(parent_root).resolve()
        parent=json.loads((parent_root/'session.json').read_text('utf-8'))
        if parent.get('checkpoint_identity') != sha256_json({k:v for k,v in parent.items() if k!='checkpoint_identity'}): raise ExplorationError('parent integrity mismatch')
        if single_action_continuation:
            inherited=lineage_accounting(parent_root)
            if parent['status']=='partial':
                previous=json.loads((parent_root/'rounds'/f"{parent['pending_round']:02d}"/'terminal.json').read_text('utf-8'))
                if previous['finish_reason']!='length' or previous['content'] or previous.get('error') or previous['http_status']!=200:
                    raise ExplorationError('only resolved length/empty-visible terminal is eligible')
            elif parent['status']=='budget_exhausted' and parent.get('termination')=='token dispatch reservation ceiling' and parent.get('pending_round') is None:
                pass  # no new request was issued; compact input only, never reset budgets
            else: raise ExplorationError('continuation parent terminal is ineligible')
            expected=asdict(budget);old_budget=dict(parent['budget'])
            if 'profile' not in old_budget and expected.get('profile') in ('standard', 'default'):
                old_budget['profile'] = expected.get('profile', 'standard')
            expected.pop('output_tokens', None);old_budget.pop('output_tokens', None)
            if old_budget!=expected: raise ExplorationError('parent hard budgets cannot reset/change')
        else:
            if parent['status']!='budget_exhausted' or parent.get('termination')!='token dispatch reservation ceiling': raise ExplorationError('continuation only for pre-dispatch token projection stop')
            parent_budget=dict(parent['budget'])
            expected_budget=asdict(budget)
            if 'profile' not in parent_budget and expected_budget.get('profile') in ('standard', 'default'):
                parent_budget['profile'] = expected_budget.get('profile', 'standard')
            if parent_budget!=expected_budget: raise ExplorationError('parent budget mismatch')
            if parent.get('pending_round') is not None: raise ExplorationError('unresolved parent dispatch')
        if parent['binding'] != {'question':question,'ru_build_identity':tools.ru_identity,'tools_identity':tools.identity}: raise ExplorationError('parent scope mismatch')
        parent_config=json.loads((parent_root/'config.json').read_text('utf-8'))
        if parent_config['provider'] != provider.config:
            raise ExplorationError('parent model/route/provider configuration cannot change')
        config['parent']={'root':str(parent_root),'run_identity':parent['run_identity'],'checkpoint_identity':parent['checkpoint_identity']}
    identity = sha256_json(config)
    if (root / 'session.json').exists():
        if sha256_json(json.loads((root / 'config.json').read_text('utf-8'))) != identity: raise ExplorationError('frozen config mismatch')
        state = json.loads((root / 'session.json').read_text('utf-8'))
        if state.get('checkpoint_identity') != sha256_json({k:v for k,v in state.items() if k != 'checkpoint_identity'}) or state['run_identity'] != identity:
            raise ExplorationError('resume identity mismatch')
        for path in root.glob('checkpoints/*.json'):
            checkpoint=json.loads(path.read_text('utf-8'))
            if path.stem != checkpoint.get('checkpoint_identity') or checkpoint['checkpoint_identity'] != sha256_json({k:v for k,v in checkpoint.items() if k!='checkpoint_identity'}):
                raise ExplorationError('immutable checkpoint integrity mismatch')
        for path in root.glob('rounds/*/terminal.json'):
            verify_response_attempts(path.parent)
            terminal=json.loads(path.read_text('utf-8'))
            if 'terminal_identity' in terminal and terminal['terminal_identity'] != sha256_json({k:v for k,v in terminal.items() if k!='terminal_identity'}):
                raise ExplorationError('terminal integrity mismatch')
            decision_path=path.parent/'decision.json'
            if decision_path.exists() and (json.loads(decision_path.read_text('utf-8'))['actions'][0] if single_action_continuation else json.loads(decision_path.read_text('utf-8'))) != json.loads(terminal['content']):
                raise ExplorationError('decision differs from immutable provider content')
        if state['status'] != 'running': return state
    else:
        if root.exists() and any(root.iterdir()): raise ExplorationError('new run root must be empty')
        root.mkdir(parents=True, exist_ok=True)
        once(root / 'config.json', config)
        state = {'schema_version': 'p05-navi-e2-session-0.1', 'run_identity': identity,
            'binding': {'question': question, 'ru_build_identity': tools.ru_identity, 'tools_identity': tools.identity},
            'status': 'running', 'events': [], 'available_ids': [start_ru], 'read_sources': [], 'pages': [], 'pending_clues': [],
            'memory': {'status': HYPOTHESIS, 'text': ''}, 'model_rounds': 0, 'provider_calls': 0, 'pending_round': None,
            'accepted_decisions': 0,
            'semantic_acceptance_status': 'not_accepted',
            'used': {'reads':0, 'searches':0, 'canonical_chars':0, 'tool_response_chars':0, 'tokens':0, 'token_reservations':0},
            'budget': asdict(budget)}
        if parent is not None:
            from copy import deepcopy
            state=deepcopy(parent)
            state.update(run_identity=identity,status='running',parent=config['parent'])
            state['schema_version']='p05-navi-e2-session-0.2'
            once(root/'parent-checkpoint.json',parent)
            if single_action_continuation:
                once(root/'parent-accounting.json',inherited)
                state['inherited_accounting']=inherited
                state['used']['tokens']=inherited['tokens']
                state['used']['token_reservations']=inherited['reservations']
                state['provider_calls']=inherited['calls']
                state['model_rounds']=inherited['calls']
                state['accepted_decisions']=parent.get('accepted_decisions',parent['model_rounds'])
                state['pending_round']=None
                state.pop('error',None)
                state['budget']=asdict(budget)
        _save(root, state)
    try:
        if not state['events']:
            seed = execute_action(tools, state, {'action':'read','unit_id':start_ru,'max_chars':20000}, budget)
            once(root / 'actions' / '000.json', state['events'][-1])
            _save(root, state)
        while state['status'] == 'running' and state['model_rounds'] < budget.rounds:
            n = state['model_rounds']
            stem = root / 'rounds' / f'{n:02d}'
            if not (stem / 'decision.json').exists():
                if (stem / 'terminal.json').exists() or (stem / 'issued.json').exists():
                    # Only a complete terminal can recover; never reissue unresolved dispatch.
                    if not (stem / 'terminal.json').exists(): raise ExplorationError('unresolved issued request; no automatic reissue')
                    terminal = json.loads((stem / 'terminal.json').read_text('utf-8'))
                else:
                    if (stem / 'request.json').exists(): raise ExplorationError('unresolved prepared attempt; preserve without reissue')
                    last = state.get('delivered_until', 0)
                    payload = {'question': question, 'model_round':n+1, 'last_round': n+1 == budget.rounds,
                        'remaining': {k: asdict(budget)[k] - (len(state['pages']) if k=='pages' else state['used'].get(k, state['model_rounds'] if k=='rounds' else 0)) for k in ('rounds','searches','reads','pages','canonical_chars','total_tokens')},
                        'known_read_sources': [{'unit_id':r['unit_id'],'address':r['source']['canonical_address'],'partial':r['partial']} for r in state['read_sources']],
                        'memory':state['memory'], 'new_tool_results': [{'action_index':e['round'],'action':e['action'],'result':model_result(e['result'])} for e in state['events'][last:]]}
                    payload['remaining']['total_tokens'] = budget.total_tokens-state['used']['tokens']
                    if single_action_continuation: payload=short_payload(state,question,budget)
                    instructions = canonical_json_bytes(config['prompt']).decode('utf-8')
                    input_text = canonical_json_bytes(payload).decode('utf-8')
                    # UTF8 byte ceiling + 256 framing tokens; conservative, not a model tokenizer estimate.
                    reserve = len(instructions.encode('utf-8')) + len(input_text.encode('utf-8')) + 256 + budget.output_tokens
                    if state['used']['tokens'] + reserve > budget.total_tokens:
                        raise ExplorationBudgetExceeded('token dispatch reservation ceiling')
                    state['pending_round'] = n
                    _save(root, state)
                    body = {'model': provider.config['route']['model_id'], 'instructions': instructions,
                            'input': input_text, 'stream': True, 'max_output_tokens': budget.output_tokens}
                    once(stem / 'input-accounting.json', {'input_utf8_token_upper_bound': reserve - budget.output_tokens, 'reserved_tokens': reserve, 'delivered_until': len(state['events'])})
                    exp_mode = "single_action" if single_action_continuation else "batch"
                    # Inspect the seam before dispatch: a TypeError after dispatch
                    # must never cause a second provider invocation.
                    import inspect
                    kwargs = {}
                    parameters = inspect.signature(provider).parameters
                    if 'decision_mode' in parameters: kwargs['decision_mode'] = exp_mode
                    if 'attempt_limit' in parameters:
                        remaining_rounds = max(1, budget.rounds - state['provider_calls'])
                        remaining_budget_attempts = max(1, (budget.total_tokens - state['used']['tokens']) // reserve)
                        kwargs['attempt_limit'] = min(remaining_rounds, remaining_budget_attempts)
                    terminal = provider(stem, body, identity, **kwargs)
                verify_response_attempts(stem)
                if 'terminal_identity' in terminal and terminal['terminal_identity'] != sha256_json({k:v for k,v in terminal.items() if k != 'terminal_identity'}): raise ExplorationError('terminal integrity mismatch')
                if not terminal['complete']: raise ExplorationError('provider partial/failure; no retry')
                if not terminal.get('decision_complete', True): raise ExplorationError('decision does not conform to exploration mode schema')
                usage = terminal.get('usage')
                total = usage.get('total_tokens') if isinstance(usage, dict) else None
                if not isinstance(total,int) or isinstance(total,bool) or total < 0: raise ExplorationError('provider total usage UNKNOWN; stop')
                try:
                    decision = json.loads(terminal['content'])
                except (json.JSONDecodeError, TypeError) as exc:
                    raise ExplorationError('provider returned invalid action JSON') from exc
                if single_action_continuation:
                    _validate_action(decision)
                    if decision.get('action') in {'clue','stop'} and len(decision.get('text',decision.get('reason','')))>500: raise ExplorationError('single action text bound')
                    decision={'actions':[decision],'memory':state['memory'],'rationale':''}
                if not isinstance(decision, dict) or set(decision) != {'actions','memory','rationale'}: raise ExplorationError('decision fields')
                if not isinstance(decision['actions'],list) or not 1 <= len(decision['actions']) <= 6: raise ExplorationError('action batch limit')
                for action in decision['actions']: _validate_action(action)
                memory = decision['memory']
                if not isinstance(memory,dict) or set(memory) != {'status','text'} or memory['status'] != HYPOTHESIS or not isinstance(memory['text'],str) or len(memory['text']) > 1600:
                    raise ExplorationError('memory must remain bounded hypothesis')
                if not isinstance(decision['rationale'],str) or len(decision['rationale']) > 1500: raise ExplorationError('rationale limit')
                once(stem / 'decision.json', decision)
            decision = json.loads((stem / 'decision.json').read_text('utf-8'))
            terminal = json.loads((stem / 'terminal.json').read_text('utf-8'))
            accounting = json.loads((stem / 'input-accounting.json').read_text('utf-8'))
            if state.get('applied_round') != n:
                state['provider_calls'] += terminal['calls']; state['used']['tokens'] += terminal['usage']['total_tokens']
                state['used']['token_reservations'] += accounting['reserved_tokens'] * terminal['calls']
                state['delivered_until'] = accounting['delivered_until']
                state['memory'] = decision['memory']; state['applied_round'] = n; state['next_action'] = 0
                if state['used']['tokens'] > budget.total_tokens: raise ExplorationBudgetExceeded('provider token usage ceiling')
                _save(root, state)
            for i in range(state['next_action'],len(decision['actions'])):
                action_path = root / 'actions' / f"{len(state['events']):03d}.json"
                if action_path.exists():
                    saved = json.loads(action_path.read_text('utf-8'))
                    if saved['action'] != decision['actions'][i] or saved.get('prior_checkpoint') != state['checkpoint_identity']: raise ExplorationError('orphan action binding mismatch')
                    state = saved['after_state']
                    state['next_action'] = i+1
                    _save(root, state)
                    continue
                from copy import deepcopy
                next_state = deepcopy(state)
                execute_action(tools, next_state, decision['actions'][i], budget)
                state = next_state
                once(action_path, state['events'][-1] | {'prior_checkpoint':state['checkpoint_identity'],'after_state':state})
                state['next_action'] = i+1
                _save(root, state)
                if state['status'] == 'stopped': break
            state['accepted_decisions'] = state.get('accepted_decisions', 0) + 1
            state['model_rounds'] = max(n+1, state['provider_calls']); state['pending_round'] = None
            _save(root, state)
        if state['status'] == 'running': state['status'] = 'round_budget_exhausted'
    except ExplorationBudgetExceeded as exc:
        state.update(status='budget_exhausted', termination=str(exc))
    except Exception as exc:
        state.update(status='partial', error={'type':type(exc).__name__, 'message':str(exc)[:1500]})
    reconcile_provider_accounting(root, state)
    _save(root, state)
    # Ledger remains inactive; no SemanticBuild or query-time comparison is run here.
    return state


def main():
    p=argparse.ArgumentParser()
    for name in ('canonical_manifest','ru_manifest','lexical_manifest','start_ru','output_root'): p.add_argument('--'+name.replace('_','-'), required=True)
    p.add_argument('--parent-root')
    p.add_argument('--single-action-continuation',action='store_true')
    p.add_argument('--route',choices=sorted({r for m,r in E2_ROUTES}), required=True)
    p.add_argument('--model',choices=sorted({m for m,r in E2_ROUTES}), required=True)
    p.add_argument('--budget-profile', choices=['standard', 'research'], default='standard')
    a=p.parse_args()
    mode = 'research' if a.budget_profile == 'research' else 'standard'
    provider = E2SDK(a.route, model=a.model, mode=mode)
    tools=LocalExplorationTools(Path(a.canonical_manifest),Path(a.ru_manifest),Path(a.lexical_manifest))
    if a.budget_profile == 'research':
        budget = E2ResearchBudget(output_tokens=4096) if a.single_action_continuation else E2ResearchBudget()
    else:
        budget = E2Budget(output_tokens=4096) if a.single_action_continuation else None
    state=run_exploration(tools,start_ru=a.start_ru,output_root=Path(a.output_root),provider=provider,parent_root=a.parent_root,
                          single_action_continuation=a.single_action_continuation,
                          budget=budget)
    print(json.dumps({k:state[k] for k in ('run_identity','status','model_rounds','provider_calls','used','pages')},ensure_ascii=False))
    return 0 if state['status']=='stopped' else 2

if __name__=='__main__': raise SystemExit(main())
