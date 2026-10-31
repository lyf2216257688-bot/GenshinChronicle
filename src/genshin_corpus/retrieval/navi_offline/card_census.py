"""Provider-free full Canonical census using the approved RU projector."""
from collections import Counter, OrderedDict
from pathlib import Path
import json
import time

from .. import retrieval_units as ru
from . import cards_v2 as v2
from .common import read, require, sha, write, seal
from .sources import Sources


class ProjectedSource(Sources):
    """Estimation only; actual preparation still verifies the stored RU snapshot."""
    def __init__(self, record, entry, rows, skips):
        self._record=record; self._rows=sorted(rows,key=lambda r:(r['source_order'],r['unit_id']))
        self._by_id={r['unit_id']:r for r in rows};self._offsets=OrderedDict()
        source=record['source_identity']['key']
        self.documents={source:{'title':record['record_metadata']['name'],'record_id':record['record_id'],
            'canonical_sha256':entry['canonical_record_sha256'],'inventory':[
                {'section':s['ordinal'],'unit':u['ordinal'],'kind':u['kind']} for s in record['sections'] for u in s['units']],
            'upstream_skips':skips}}

    def record(self, source):return self._record
    def rows(self, source):return self._rows
    def row(self, uid):return self._by_id[uid]


def census(out, canonical, ru_manifest, *, body_limit=8000, request_limit=24000):
    out=Path(out);require(not out.exists(),'New census directory required');out.mkdir(parents=True)
    started=time.perf_counter()
    manifest=read(canonical);entries=ru._validate_manifest(manifest); rm=read(ru_manifest)
    cfg=ru.RetrievalUnitBuildConfig(**{k:rm['projection_config'][k] for k in ('text_fragment_chars','structured_fragment_chars')})
    identity=ru._canonical_input_identity(manifest,entries)
    require(rm['canonical_input']['canonical_input_identity']==identity and
            rm['build_identity']==ru._build_identity(manifest,identity,cfg),'Census RU input identity differs')
    records=[];failures=[];labels=Counter();kinds=Counter(); reasons=Counter()
    path=out/'sources.jsonl'
    with path.open('x',encoding='utf8') as handle:
        for i,entry in enumerate(entries):
            row={'source_id':entry['parsed_identity_key'],'canonical_sha256':entry['canonical_record_sha256']}
            try:
                record=ru._load_record(entry,i)
                rows,skips=ru._validate_record(record,entry,i,cfg,source=manifest['source'],locale=manifest['locale'])
                s=ProjectedSource(record,entry,rows,skips);source=entry['parsed_identity_key']
                plan=v2.reading_plan(s,source)
                batches=list(v2.read_batches(s,source,plan,body_limit,request_limit))
                groups=Counter(section for section,_,_ in batches)
                # Exact reading calls. Synthesis tree fan-out depends on future model output.
                minimum_synthesis=(sum(n>1 for section,n in groups.items() if section is not None)+1
                                   if batches and not (len(batches)==1 and set(groups)=={None}) else 0)
                row.update(title=record['record_metadata']['name'],ru_count=len(rows),
                    raw_chars=sum(len(r['retrieval_visible_text']) for r in rows),
                    selected_chars=sum(len(q['text']) for q in plan['selected']),
                    read_calls=len(batches),minimum_synthesis_calls=minimum_synthesis,
                    exact_read_wire_chars=sum(len(json.dumps(v2.wire(v),ensure_ascii=False)) for _,v,_ in batches),
                    exact_read_wire_utf8_bytes=sum(len(json.dumps(v2.wire(v),ensure_ascii=False).encode()) for _,v,_ in batches),
                    sections=len(groups),upstream_skips=len(skips),
                    status='ELIGIBLE' if batches else 'NO_ELIGIBLE_TEXT_MANAGEMENT_NODE_ONLY')
                labels.update(s['source_metadata'].get('name','') for s in record['sections'])
                kinds.update(u['kind'] for section in record['sections'] for u in section['units'])
                reasons.update(u['reason'] for u in plan['units'])
                records.append(row)
            except (ValueError,KeyError,ru.RetrievalUnitError) as exc:
                row.update(status='QUARANTINED',error=type(exc).__name__+': '+str(exc));failures.append(row)
            handle.write(json.dumps(row,ensure_ascii=False)+'\n');handle.flush()
    def distribution(key):
        values=sorted(r[key] for r in records)
        return {'sum':sum(values),'max':max(values,default=0),
                **{f'p{p}':values[min(len(values)-1,int((len(values)-1)*p/100))] if values else 0 for p in (50,90,95,99)}}
    summary={'card_version':v2.VERSION,'reading_policy':v2.READING_POLICY,'prompt_sha256':__import__('hashlib').sha256(v2.PROMPT.encode()).hexdigest(),
        'canonical_manifest_sha256':sha(canonical),'ru_manifest_sha256':sha(ru_manifest),
        'records':len(entries),'eligible_sources':sum(r['status']=='ELIGIBLE' for r in records),
        'management_only_sources':sum(r['status']!='ELIGIBLE' for r in records),'quarantined':len(failures),
        'metrics':{key:distribution(key) for key in ('ru_count','raw_chars','selected_chars','read_calls','minimum_synthesis_calls',
                                                    'exact_read_wire_chars','exact_read_wire_utf8_bytes')},
        'unit_kinds':dict(kinds),'section_labels':dict(labels.most_common()),'selection_reasons':dict(reasons),
        'elapsed_seconds':round(time.perf_counter()-started,3),'paid_requests':0,
        'basis':'HASH_VERIFIED_CANONICAL_APPROVED_RU_REPROJECTION; stored RU rows not scanned by this estimation',
        'synthesis_task_count':'LOWER_BOUND; intermediate reduction depends on actual generated child sizes',
        'actual_fee':'NOT_INCURRED; future invoice UNKNOWN'}
    write(out/'summary.json',summary);write(out/'quarantined.json',failures);seal(out)
    return summary
