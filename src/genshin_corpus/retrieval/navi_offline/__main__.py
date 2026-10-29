"""PowerShell-compatible explicit offline workflow; live is opt-in and separately authorized."""
import argparse
import json
from pathlib import Path

from . import pipeline


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='action',required=True)
    prepare=sub.add_parser('prepare')
    prepare.add_argument('--run',type=Path,required=True)
    prepare.add_argument('--canonical',type=Path,default=Path(pipeline.CANONICAL))
    prepare.add_argument('--ru',type=Path,default=Path(pipeline.RU))
    prepare.add_argument('--sources',nargs='+',required=True)
    prepare.add_argument('--device',choices=['cuda','cpu'],default='cuda')
    prepare.add_argument('--model-path',type=Path,default=Path(pipeline.MODEL))
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
    action=args.pop('action');root=args.pop('run')
    if action=='prepare':
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
