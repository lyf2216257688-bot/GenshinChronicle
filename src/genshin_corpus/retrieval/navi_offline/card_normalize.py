"""Metadata-only final Card consolidation; original model artifacts stay immutable."""
from copy import deepcopy
from pathlib import Path

from . import anchors,card_generation as generation,card_contract
from .common import read,write,copy,seal,verify,sha,digest,require

POLICY='card-generation-contract-annotation-1'
META={'quote_contract','generation_contracts','generation_derivation'}
LISTS=('cards','reading_batches','synthesis_nodes')

def wire_contract(plan):
    strategy=plan.get('strategy')
    require(strategy in generation.EVIDENCE_STRATEGIES,'Unsupported source generation contract')
    return generation.quote_contract(strategy)

def normalize(value,job_root):
    """Describe each Card's actual wire generation, including reused old parts."""
    job_root=Path(job_root);result=deepcopy(value);nodes={}
    for field in LISTS:
        for c in result.get(field,[]):
            if c['card_id'] in nodes:require(nodes[c['card_id']]==c,'Conflicting Card identity')
            nodes[c['card_id']]=c
    memo={}
    def contracts(cid,path=()):
        require(cid not in path,'Cyclic generation metadata inheritance')
        if cid in memo:return memo[cid]
        c=nodes[cid];entries=[]
        for part in c['parts']:
            folder=job_root/part['job_id'];verify(folder);plan=read(folder/'plan.json')
            require(read(folder/'result.json')['state']=='CONTENT_VERIFIED','Metadata cannot accept invalid model output')
            entries.append({'job_id':part['job_id'],'generation_strategy':plan['strategy'],
                'quote_contract':wire_contract(plan),'prompt_sha256':plan['prompt_sha256'],'schema_sha256':plan['schema_sha256']})
        if not entries and c['level']=='clue':
            require(c['parent_id'] in nodes,'Missing program-derived clue parent')
            entries=deepcopy(contracts(c['parent_id'],path+(cid,)))
        memo[cid]=entries;return entries
    changed=[]
    for cid,c in nodes.items():
        entries=contracts(cid)
        kinds=set(e['quote_contract'] for e in entries)
        actual=next(iter(kinds)) if len(kinds)==1 else 'MIXED_GENERATION_CONTRACTS' if kinds else 'NO_MODEL_GENERATION'
        if c.get('quote_contract')!=actual:changed.append({'card_id':cid,'original':c.get('quote_contract'),'actual':actual})
        c.update(quote_contract=actual,generation_contracts=deepcopy(entries),
            generation_derivation='PARENT_OPEN_QUESTION' if c['level']=='clue' and not c['parts'] else 'MODEL_PARTS')
    for field in LISTS:
        if field in result:result[field]=[nodes[c['card_id']] for c in result[field]]
    require(semantic_bytes(result)==semantic_bytes(value),'Normalization altered Card semantics or evidence')
    return result,changed

def semantic_bytes(value):
    result=deepcopy(value)
    for field in LISTS:
        for c in result.get(field,[]):
            for key in META:c.pop(key,None)
    return digest(result)

def import_normalized_source(root,source,prior,sources):
    """New sealed source revision; neither original Cards nor accepted pointers change."""
    from .card_recovery import import_job,validate_dependencies
    root=Path(root);prior=Path(prior);base=prior/'documents'/digest(source)
    ref=read(base/'accepted-build.json');folder=base/ref['directory'];verify(folder)
    require(sha(folder/'complete.json')==ref['sha256'],'Original source revision differs')
    validate_dependencies(prior,folder,source,sources)
    original=read(folder/'cards.json')
    for field in LISTS:
        for card in original.get(field,[]):
            for part in card['parts']:
                job=prior/'jobs'/part['job_id']
                require(read(job/'result.json').get('authority')=='LIVE_NOT_HUMAN_REVIEWED',
                    'Final real experiment collection cannot import mock outputs')
                import_job(root,job,sources)
    value,changed=normalize(original,root/'jobs')
    target=root/'documents'/digest(source)/'attempt-0000'
    write(target/'cards.json',value);copy(folder/'reading-plan.json',target/'reading-plan.json')
    write(target/'normalization-origin.json',{'policy':POLICY,'source_id':source,'original':str(folder.resolve()),
        'original_pointer_sha256':sha(base/'accepted-build.json'),'original_complete_sha256':sha(folder/'complete.json'),
        'original_cards_sha256':sha(folder/'cards.json'),'normalizer_sha256':sha(Path(__file__)),
        'unchanged_semantics_evidence_parts_coverage':semantic_bytes(original),'changed_contract_annotations':changed,
        'new_model_calls':0,'navigation_status':'INACTIVE','human_acceptance':'NOT_REVIEWED'})
    seal(target);write(target.parent/'accepted-build.json',{'directory':target.name,'sha256':sha(target/'complete.json'),'status':'COMPLETE'})
    return {'source_id':source,'changed_annotations':len(changed),'new_model_calls':0}

def audit(root):
    root=Path(root);verify(root/'prepared');config=read(root/'prepared/config.json');count=0;changed=0
    require(config['code'].get(Path(__file__).name)==sha(Path(__file__)),'Normalizer code differs from final preparation')
    for source in config['sources']:
        base=root/'documents'/digest(source);ref=read(base/'accepted-build.json');folder=base/ref['directory'];verify(folder)
        require(ref['sha256']==sha(folder/'complete.json'),'Final source pointer differs')
        origin=read(folder/'normalization-origin.json');prior=Path(origin['original']);verify(prior)
        require(sha(prior/'complete.json')==origin['original_complete_sha256'] and sha(prior/'cards.json')==origin['original_cards_sha256'],
                'Normalization origin changed')
        require(sha(prior.parent/'accepted-build.json')==origin['original_pointer_sha256'],'Original source pointer changed')
        value=read(folder/'cards.json');expected,edits=normalize(read(prior/'cards.json'),root/'jobs')
        require(value==expected and origin['changed_contract_annotations']==edits,'Final generation-contract metadata differs')
        require(sha(folder/'reading-plan.json')==sha(prior/'reading-plan.json'),'Reading policy changed during consolidation')
        for field in LISTS:
            for c in value.get(field,[]):
                require(c['navigation_status']=='INACTIVE' and c['human_acceptance']=='NOT_REVIEWED' and c['semantic_quality']=='NOT_VALIDATED',
                        'Consolidation cannot authorize acceptance')
        changed+=len(edits);count+=1
    require(not list((root/'model-call-budget/requests').glob('*/reservation.json')),'Consolidation unexpectedly reserved a model call')
    return {'normalization_integrity':'PASS','completed_sources':count,'changed_contract_annotations':changed,
        'semantics_evidence_parts_and_coverage':'UNCHANGED','new_model_calls':0,'semantic_quality':'NOT_VALIDATED'}
