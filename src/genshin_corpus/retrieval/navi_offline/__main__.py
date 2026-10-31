"""PowerShell-compatible explicit offline workflow; live is opt-in and separately authorized."""
import argparse
import json
from pathlib import Path

from . import pipeline, card_build, card_census


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='action',required=True)
    p=sub.add_parser('card-prepare-snapshot')
    p.add_argument('--run',type=Path,required=True);p.add_argument('--snapshot',type=Path,required=True)
    p.add_argument('--total-request-limit',type=int,required=True)
    p.add_argument('--body-limit',type=int,default=8000)
    p.add_argument('--request-limit',type=int,default=100000)
    for name in ('card-prepare','card-census'):
        p=sub.add_parser(name)
        p.add_argument('--canonical',type=Path,default=Path(pipeline.CANONICAL))
        p.add_argument('--ru',type=Path,default=Path(pipeline.RU))
        p.add_argument('--body-limit',type=int,default=8000)
        p.add_argument('--request-limit',type=int,default=24000)
        if name=='card-prepare':
            p.add_argument('--run',type=Path,required=True)
            p.add_argument('--sources',nargs='+')
            p.add_argument('--revision',default='initial')
            p.add_argument('--reason',default='')
            p.add_argument('--regenerate-jobs',nargs='*',default=[])
            p.add_argument('--generation-strategy',choices=['legacy-responses','deepseek-card-structured-1','qwen-card-structured-1'],default='deepseek-card-structured-1')
            p.add_argument('--total-request-limit',type=int)
            p.add_argument('--combine-sections',action='store_true')
        else:p.add_argument('--out',type=Path,required=True)
    for name in ('card-run','card-resume'):
        p=sub.add_parser(name)
        p.add_argument('--run',type=Path,required=True)
        p.add_argument('--backend',choices=['mock','live'],default='mock')
        p.add_argument('--max-new-requests',type=int,default=20)
        p.add_argument('--reuse',nargs='*',type=Path,default=[])
        p.add_argument('--execute-authorized',action='store_true')
        p.add_argument('--ack-plan-sha256')
        p.add_argument('--max-provider-tokens',type=int)
        p.add_argument('--call-policy',type=Path,help='Shared retry policy JSON; live authorization is still required')
    p=sub.add_parser('card-audit');p.add_argument('--run',type=Path,required=True)
    p=sub.add_parser('card-index')
    p.add_argument('--run',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--device',choices=['cuda','cpu'],default='cuda')
    p.add_argument('--model-path',type=Path,default=Path(pipeline.MODEL))
    p.add_argument('--embedding',choices=['mock','local'],default='local')
    p.add_argument('--reuse-from',type=Path)
    prepare=sub.add_parser('prepare')
    prepare.add_argument('--run',type=Path,required=True)
    prepare.add_argument('--canonical',type=Path,default=Path(pipeline.CANONICAL))
    prepare.add_argument('--ru',type=Path,default=Path(pipeline.RU))
    prepare.add_argument('--sources',nargs='+',required=True)
    prepare.add_argument('--device',choices=['cuda','cpu'],default='cuda')
    prepare.add_argument('--model-path',type=Path,default=Path(pipeline.MODEL))
    prepare.add_argument('--retrieval-policy',choices=['legacy',pipeline.story_retrieval.POLICY],default='legacy')
    prepare.add_argument('--attribution-check',action='store_true',help='One separately budgeted auxiliary check per nonempty Link')
    for name,default in [('max-ru',512),('max-cards',64),('max-pairs',20),('max-card-jobs',32),('body-limit',8000)]:
        prepare.add_argument('--'+name,type=int,default=default)
    for action in ('run','resume'):
        p=sub.add_parser(action)
        p.add_argument('--run',type=Path,required=True)
        p.add_argument('--backend',choices=['mock','replay','live'],default='mock')
        p.add_argument('--embedding',choices=['local','mock'],default='local')
        p.add_argument('--replay-root',type=Path)
        p.add_argument('--execute-authorized',action='store_true')
        p.add_argument('--ack-plan-sha256')
        p.add_argument('--max-provider-tokens',type=int)
    audit=sub.add_parser('audit');audit.add_argument('--run',type=Path,required=True)
    export=sub.add_parser('export-review');export.add_argument('--run',type=Path,required=True)
    export.add_argument('--out',type=Path,required=True)
    review=sub.add_parser('record-review');review.add_argument('--run',type=Path,required=True)
    review.add_argument('--item-id')
    for name in ('artifact','reviewer','timestamp','decision','reason'):
        review.add_argument('--'+name,required=True)
    args=vars(parser.parse_args())
    action=args.pop('action');root=args.pop('run',None)
    if action=='card-prepare-snapshot':
        result=card_build.prepare_snapshot(root,**args)
    elif action=='card-census':
        result=card_census.census(args.pop('out'),args.pop('canonical'),args.pop('ru'),**args)
    elif action=='card-prepare':
        if args.get('generation_strategy')=='legacy-responses':args['generation_strategy']=None
        result=card_build.prepare(root,args.pop('canonical'),args.pop('ru'),args.pop('sources'),**args)
    elif action in {'card-run','card-resume'}:
        result=card_build.run(root,backend=args['backend'],max_new_requests=args['max_new_requests'],reuse=args['reuse'],
            live_authorized=args['execute_authorized'],ack_plan=args['ack_plan_sha256'],max_provider_tokens=args['max_provider_tokens'],
            call_policy=card_build.model_calls.Policy(**card_build.read(args['call_policy'])) if args['call_policy'] else None)
    elif action=='card-audit':result=card_build.audit(root)
    elif action=='card-index':
        if args.pop('embedding')=='mock':
            from .testing import MockEncoder
            encoder=MockEncoder()
        else:
            from .embedding import LocalEncoderProcess
            encoder=LocalEncoderProcess(pipeline.REPO/'.local/embedding-runtime/Scripts/python.exe',args['model_path'],args['device'],
                                         stderr_path=Path(root)/'card-embedding.stderr.log')
        try:result=card_build.build_index(root,args['out'],encoder,reuse_from=args['reuse_from'])
        finally:
            if hasattr(encoder,'close'):encoder.close()
    elif action=='prepare':
        result=pipeline.prepare(root,args.pop('canonical'),args.pop('ru'),args.pop('sources'),**args)
    elif action in {'run','resume'}:
        embedding=args.pop('embedding')
        from .common import require
        require(not (args['backend']=='live' and embedding=='mock'),'Live cannot use mock vectors')
        encoder=None
        if embedding=='mock':
            from .testing import MockEncoder
            encoder=MockEncoder()
        result=pipeline.run(root,encoder,backend=args['backend'],replay_root=args['replay_root'],
                            live_authorized=args['execute_authorized'],ack_plan=args['ack_plan_sha256'],
                            max_provider_tokens=args['max_provider_tokens'])
    elif action=='audit':
        result=pipeline.audit(root)
    elif action=='export-review':
        result=pipeline.export_review(root,args['out'])
    else:
        result=pipeline.record_review(root,**args)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    if result.get('failures',0) or result.get('archive_integrity','PASS')!='PASS':
        raise SystemExit(2)


if __name__=='__main__':
    main()
