"""Versioned typed-source/Card adapter over existing raw-first protocol primitives.

The old v0.3 runner and its historical executable hashes remain untouched.
No automatic retries; live calls require a separately approved exact campaign.
"""
import json
from pathlib import Path
import time

import httpx
import openai

from .. import navi_prompt_candidate as candidate
from .. import navi_v03_runner as old
from .. import navi_f2_sdk_transport as transport
from ..navi_v02_message_verifier import inspect
from . import cards
from .common import copy, digest, inventory, read, require, seal, sha, verify, write

VERSION = 'p05-navi-typed-source-runner-1'


def mock_transport(value):
    raw=''.join('data: '+json.dumps(e,ensure_ascii=False)+'\n\n' for e in mock_events(json.dumps(value,ensure_ascii=False))).encode()
    class Body(httpx.SyncByteStream):
        def __iter__(self):
            yield raw
    return httpx.MockTransport(lambda req:httpx.Response(200,headers={'content-type':'text/event-stream'},stream=Body()))


def native_binding(sources, view, evidence):
    """Adapt exact rich-text quotes to the unchanged historical source contract."""
    rows={}
    aliases={}
    bound={}
    paths={}
    sides={}
    for side in ('A','B'):
        q=evidence[view[side]['snippets'][0]['id']]
        source=q['source_id'];cid=source.rsplit(':',1)[-1]
        require(cid.isdecimal(),'Old runner requires verified OBC numeric IDs')
        sides[side]=cid
        paths[cid]=sources.root/sources.documents[source]['canonical_file']
        for snippet in view[side]['snippets']:
            q=evidence[snippet['id']]
            require(q['canonical_address']['canonical_unit_kind']=='rich_text','Non-rich quote cannot enter old runner')
            sources.verify_quote(q)
            same=[r for r in sources.rows(source) if r['source']['canonical_address']==q['canonical_address']]
            same.sort(key=lambda r:r['fragment_selector'].get('fragment_index',0))
            offset=0;official=[]
            for row in same:
                end=offset+len(row['retrieval_visible_text'])
                rows[row['unit_id']]=row
                if offset<q['canonical_end'] and end>q['canonical_start']:
                    official.append(row['unit_id'])
                offset=end
            eid=q['id'];aliases[snippet['id']]=eid
            bound[eid]={'id':eid,'source':cid,'record_id':q['record_id'],'canonical_sha256':q['canonical_sha256'],
                        'section':q['canonical_address']['section_ordinal'],'unit':q['canonical_address']['canonical_unit_ordinal'],
                        'start':q['canonical_start'],'end':q['canonical_end'],'text':q['text'],
                        'text_sha256':q['text_sha256'],'official_unit_ids':official}
    return {'packet':{'sides':sides,'payload':{s:view[s] for s in ('A','B')},'aliases':aliases},
            'evidence':bound,'ru_rows':rows},paths


def execute_native(root, stage, wire, view, evidence, sources, *, backend, mock_value,
                   candidate_freeze, live_authorized=False, api_key=None, ledger=None):
    """Rich-text production compatibility path invokes the original runner itself."""
    root=Path(root)
    plan={'version':VERSION,'stage':stage,'wire':wire,'view':view,'evidence':evidence,
          'fingerprint':digest(wire),'backend':backend,'automatic_retry':False,'executor':'UNCHANGED_V03_RUNNER'}
    if (root/'complete.json').exists():
        verify(root);require(read(root/'plan.json')==plan,'Native request identity changed')
        old.audit_run(root/'native-run')
        return read(root/'result.json')
    require(not root.exists(),'Pending native request: never reissue')
    require(backend in {'mock','live'},'Native replay uses the archived typed envelope')
    if backend=='live':
        require(live_authorized and bool(api_key),'Live authorization missing')
        ledger=old.LEDGER
    root.mkdir(parents=True)
    write(root/'plan.json',plan)
    binding,paths=native_binding(sources,view,evidence)
    old.prepare(root/'native-plan',view,binding,paths,request_id=digest([stage,wire]),mode=stage,candidate_freeze=candidate_freeze)
    require(read(root/'native-plan/request.json')['wire']==wire,'Native prepared wire differs')
    result=old.run_case(root/'native-plan',root/'native-run',api_key=api_key if backend=='live' else 'offline-mock',
                        send_authorized=True,acknowledge_plan_sha256=sha(root/'native-plan/plan.json'),ledger=ledger,
                        transport_factory=(lambda:mock_transport(mock_value)) if backend=='mock' else None)
    result=result | {'authority':'MOCK_NOT_MODEL_OUTPUT' if backend=='mock' else 'LIVE_NOT_HUMAN_REVIEWED'}
    attempt=sorted((root/'native-run/attempts').iterdir())[-1]
    for name in ('request.body.bin','http.json','response.body.raw','sdk-events.jsonl'):
        if (attempt/name).exists():copy(attempt/name,root/name)
    if result['state']=='CONTENT_VERIFIED':
        copy(attempt/'candidate.json',root/'value.json')
    write(root/'result.json',result);seal(root)
    return result


def mock_events(text):
    message = {'id':'mock-message','type':'message','role':'assistant','status':'completed',
               'content':[{'type':'output_text','text':text}]}
    ix = {'item_id':message['id'],'output_index':0,'content_index':0}
    response = {'id':'mock-response','status':'completed','model':'deepseek-v4.1-flash','output':[message],
                'usage':{'input_tokens':3,'output_tokens':2,'total_tokens':5}}
    return [{'type':'response.created','response':{'id':'mock-response'}},
            {'type':'response.output_item.added','output_index':0,'item':message},
            {'type':'response.output_text.delta',**ix,'delta':text},
            {'type':'response.output_text.done',**ix,'text':text},
            {'type':'response.content_part.done',**ix,'part':message['content'][0]},
            {'type':'response.output_item.done','output_index':0,'item':message},
            {'type':'response.completed','response':response}]


def checked_stream(folder, cap):
    folder = Path(folder)
    encoding = read(folder/'http.json')['headers'].get('content-encoding','')
    events = old.c1.sse_events(transport.decoded_body((folder/'response.body.raw').read_bytes(),encoding))
    sdk = [candidate.strict_json(line) for line in (folder/'sdk-events.jsonl').read_text(encoding='utf8').splitlines()]
    checked = inspect(events,sdk)
    require(checked['full_stream']=='PASS','Full raw/SDK stream validation failed')
    terminal = events[-1]['response']
    require(events[0]['type']=='response.created' and terminal['model']=='deepseek-v4.1-flash','Wrong stream/model')
    indexed = {'response.output_item.added','response.output_item.done','response.content_part.added',
               'response.content_part.done','response.output_text.delta','response.output_text.done',
               'response.reasoning_summary_part.added','response.reasoning_summary_part.done',
               'response.reasoning_summary_text.delta','response.reasoning_summary_text.done',
               'response.reasoning_text.delta','response.reasoning_text.done'}
    require(all(e['type'] in indexed|{'response.created','response.in_progress','response.completed'} for e in events),'Unsupported stream event')
    items = terminal['output']
    for i,item in enumerate(items):
        added=[j for j,e in enumerate(events) if e['type']=='response.output_item.added' and e.get('output_index')==i]
        done=[j for j,e in enumerate(events) if e['type']=='response.output_item.done' and e.get('output_index')==i]
        require(len(added)==len(done)==1 and added[0]<done[0]<len(events)-1 and events[done[0]]['item']==item,'Output item order/snapshot differs')
        if item['type']=='message':
            require(len(item['content'])==1 and item['content'][0]['type']=='output_text','Unsupported message content')
    seq=[e.get('sequence_number') for e in events]
    if any(x is not None for x in seq):
        require(all(type(x) is int for x in seq) and seq==list(range(seq[0],seq[0]+len(seq))),'Event sequence differs')
    for e in events:
        if e['type'] in indexed:
            require('output_index' in e,'Missing event binding')
            if e['type'] not in {'response.output_item.added','response.output_item.done'}:
                require('item_id' in e,'Missing event item ID')
        if 'output_index' in e:
            i=e['output_index']
            require(type(i) is int and 0<=i<len(items),'Invalid output index')
            require('item_id' not in e or e['item_id']==items[i]['id'],'Wrong event item ID')
        if e['type'] in {'response.created','response.in_progress'}:
            require(e['response']['id']==terminal['id'],'Response ID changed')
    require(old.valid_usage(checked['usage'],cap),'Invalid usage')
    return checked


def validate_content(stage, text, view):
    if stage=='card':
        return cards.validate(candidate.strict_json(text),view)
    return candidate.validate_output(text,{s:[q['id'] for q in view[s]['snippets']] for s in ('A','B')},
                                     view.get('candidates',[]),mode=stage)


def execute(root, stage, wire, view, evidence, *, backend, mock_value=None, replay_root=None,
            live_authorized=False, api_key=None, transport_override=None, ledger=None):
    root=Path(root)
    require(stage in {'card','generation','review'} and backend in {'mock','replay','live'},'Invalid execution mode')
    fingerprint=digest(wire)
    plan={'version':VERSION,'stage':stage,'wire':wire,'view':view,'evidence':evidence,
          'fingerprint':fingerprint,'backend':backend,'automatic_retry':False}
    if (root/'complete.json').exists():
        verify(root)
        require(read(root/'plan.json')==plan,'Completed request identity changed')
        result=read(root/'result.json')
        if result['state']=='CONTENT_VERIFIED':
            checked=checked_stream(root,wire['max_output_tokens'])
            require(validate_content(stage,checked['message_text'],view)==read(root/'value.json'),'Content replay differs')
        return result
    require(not root.exists(),'Pending request reservation: never automatically reissue')
    if backend=='live':
        require(live_authorized is True and bool(api_key),'Live authority/credential missing')
        require(api_key not in json.dumps(wire,ensure_ascii=False),'Credential in input')
        ledger=Path(ledger) if ledger is not None else old.LEDGER
        require(ledger.resolve()==old.LEDGER.resolve(),'Live calls must use the persistent cross-run ledger')
    elif backend=='mock':
        require(transport_override is not None or mock_value is not None,'Mock response required')
        ledger=Path(ledger) if ledger is not None else root.parent.parent/'mock-ledger'
        require(not ledger.resolve().is_relative_to(old.LEDGER.resolve()),'Mock cannot poison live ledger')
    if backend=='replay':
        require(replay_root is not None,'Replay root required')
        replay_root=Path(replay_root)
        options=[p.parent for p in replay_root.glob('*/plan.json') if read(p).get('fingerprint')==fingerprint]
        direct=replay_root/fingerprint
        if direct.exists() and direct not in options:options.append(direct)
        require(len(options)==1,'Recorded request missing/ambiguous in replay root')
        source=options[0]
        verify(source)
        prior=read(source/'plan.json')
        require(all(prior[k]==plan[k] for k in ('stage','wire','view','evidence','fingerprint')),'Recorded request/source identity differs')
        root.mkdir(parents=True)
        write(root/'plan.json',plan)
        for name in ('request.body.bin','http.json','response.body.raw','sdk-events.jsonl'):
            copy(source/name,root/name)
        try:
            require(candidate.strict_json((root/'request.body.bin').read_text(encoding='utf8'))==wire,'Recorded actual body differs from wire')
            checked=checked_stream(root,wire['max_output_tokens'])
            value=validate_content(stage,checked['message_text'],view)
            write(root/'value.json',value)
            result={'state':'CONTENT_VERIFIED','usage':checked['usage'],'authority':'RECORDED_RESPONSE_REPLAY'}
        except Exception as exc:
            result={'state':'REPLAY_INVALID','usage':'UNKNOWN','diagnostics':transport.diagnostics(exc)}
        write(root/'result.json',result)
        seal(root)
        return result
    claim=ledger/fingerprint
    claim.mkdir(parents=True,exist_ok=False)
    write(claim/'reservation.json',{'fingerprint':fingerprint,'root':str(root.resolve()),'backend':backend,'state':'RESERVED_NO_AUTO_RESUME'})
    root.mkdir(parents=True)
    write(root/'plan.json',plan)
    state={'http_status':None,'calls':0}
    trace=[]
    started=time.monotonic()
    result={'state':'UNKNOWN','usage':'UNKNOWN','authority':'MOCK_NOT_MODEL_OUTPUT' if backend=='mock' else 'LIVE_NOT_HUMAN_REVIEWED'}

    def request_hook(req):
        state['calls']+=1
        require(state['calls']==1 and req.method=='POST' and str(req.url)==old.c1.BASE_URL+'/responses' and
                candidate.strict_json(req.content.decode())==wire,'SDK request differs')
        req.extensions['trace']=lambda name,info:trace.append(name)
        with (root/'request.body.bin').open('xb') as h:
            h.write(req.content);h.flush();__import__('os').fsync(h.fileno())
        write(root/'request-metadata.json',{'url':str(req.url),'sdk_retries':0,'transport_retries':0,'trust_env':False,'redirects':False})

    def response_hook(res):
        state['http_status']=res.status_code
        write(root/'http.json',{'status':res.status_code,'headers':{k:res.headers[k] for k in ('content-type','content-encoding','x-request-id') if k in res.headers}})
        require(not res.is_stream_consumed,'Response consumed before archival')
        res.stream=transport.RawTee(res.stream,root/'response.body.raw')

    if transport_override is None and backend=='mock':
        transport_override=mock_transport(mock_value)
    try:
        underlying=transport_override if transport_override is not None else httpx.HTTPTransport(trust_env=False,retries=0)
        with httpx.Client(transport=underlying,trust_env=False,follow_redirects=False,timeout=httpx.Timeout(180,connect=15),
                          event_hooks={'request':[request_hook],'response':[response_hook]}) as client:
            with openai.OpenAI(base_url=old.c1.BASE_URL,api_key=api_key if backend=='live' else 'offline-mock',max_retries=0,http_client=client) as sdk:
                with sdk.responses.create(**wire) as stream:
                    with (root/'sdk-events.jsonl').open('x',encoding='utf8') as h:
                        for e in stream:
                            h.write(json.dumps(e.model_dump(mode='json',exclude_unset=True),ensure_ascii=False)+'\n')
                            h.flush();__import__('os').fsync(h.fileno())
                            require(time.monotonic()-started<=180,'Request deadline')
        require(state['http_status']==200,'HTTP rejected')
        checked=checked_stream(root,wire['max_output_tokens'])
        result['usage']=checked['usage']
        value=validate_content(stage,checked['message_text'],view)
        write(root/'value.json',value)
        result['state']='CONTENT_VERIFIED'
    except Exception as exc:
        result['diagnostics']=transport.diagnostics(exc)
        if state['http_status'] is not None:
            result['state']='HTTP_REJECTED' if state['http_status']!=200 else 'STREAM_OR_CONTENT_INVALID'
        # Keep terminal observations even when strict validation cannot finish.
        if (root/'http.json').exists() and (root/'response.body.raw').exists():
            try:
                encoding=read(root/'http.json')['headers'].get('content-encoding','')
                events=old.c1.sse_events(transport.decoded_body((root/'response.body.raw').read_bytes(),encoding))
                result['terminal_observations']=[e['response'] for e in events if e['type'] in {'response.completed','response.incomplete','response.failed'}]
            except (ValueError,KeyError):
                pass
    result.update(state,elapsed_seconds=round(time.monotonic()-started,3),automatic_retry=False,
                  human_acceptance='NOT_REVIEWED',navigation_status='INACTIVE',actual_fee='UNKNOWN')
    write(root/'trace.json',{'events':trace})
    write(root/'result.json',result)
    seal(root)
    write(claim/'outcome.json',{'root_complete_sha256':sha(root/'complete.json'),'state':result['state']})
    return result
