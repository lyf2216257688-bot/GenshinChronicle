"""Run a verified, operator-owned prepared runtime in an isolated interpreter."""
import json
from pathlib import Path
import subprocess
import sys
from dataclasses import asdict

from .common import read, require, sha, verify

BOOTSTRAP='''
import importlib, importlib.util, inspect, json, sys
from pathlib import Path
payload=json.load(sys.stdin)
root=Path(payload['root']); snapshot=root/'prepared/execution-code'
from genshin_corpus.evidence_io import verify, read, sha, require
verify(root/'prepared')
config=read(root/'prepared/config.json')
require({p.name:sha(p) for p in snapshot.glob('*.py')}==config['code'],'Archived runtime identity differs')
for name in ('evidence_io','model_calls'):
    path=snapshot/(name+'.py')
    if path.exists():
        key='genshin_corpus.'+name
        spec=importlib.util.spec_from_file_location(key,path)
        module=importlib.util.module_from_spec(spec);sys.modules[key]=module
        spec.loader.exec_module(module)
        setattr(importlib.import_module('genshin_corpus'),name,module)
package=importlib.import_module('genshin_corpus.retrieval.navi_offline')
package.__path__=[str(snapshot)]
build=importlib.import_module('genshin_corpus.retrieval.navi_offline.card_build')
require(build.code_identity()==config['code'],'Loaded runtime differs')
kwargs=payload['kwargs']
if kwargs.get('call_policy') is not None:
    from genshin_corpus.model_calls import Policy
    kwargs['call_policy']=Policy(**kwargs['call_policy'])
if 'call_policy' not in inspect.signature(build.run).parameters:
    require(kwargs.pop('call_policy',None) is None,'Frozen runtime does not support shared policy migration')
print(json.dumps(build.run(root,**kwargs),ensure_ascii=False))
'''


AUDIT_BOOTSTRAP=BOOTSTRAP.replace(
    'print(json.dumps(build.run(root,**kwargs),ensure_ascii=False))',
    'print(json.dumps(build.audit(root),ensure_ascii=False))')


def archived_audit(root):
    root=Path(root).resolve();verify(root/'prepared')
    config=read(root/'prepared/config.json');snapshot=root/'prepared/execution-code'
    require({p.name:sha(p) for p in snapshot.glob('*.py')}==config['code'],'Archived audit identity differs')
    process=subprocess.run([sys.executable,'-B','-c',AUDIT_BOOTSTRAP],
        input=json.dumps({'root':str(root),'kwargs':{}}),
        capture_output=True,text=True,encoding='utf8',check=False)
    require(process.returncode==0,'Archived audit failed: '+process.stderr[-3000:])
    return json.loads(process.stdout)


def archived_run(root,**kwargs):
    root=Path(root).resolve();verify(root/'prepared')
    config=read(root/'prepared/config.json');snapshot=root/'prepared/execution-code'
    require({p.name:sha(p) for p in snapshot.glob('*.py')}==config['code'],'Archived runtime identity differs')
    kwargs['reuse']=[str(Path(p).resolve()) for p in kwargs.get('reuse',())]
    if kwargs.get('call_policy') is not None:kwargs['call_policy']=asdict(kwargs['call_policy'])
    process=subprocess.run([sys.executable,'-B','-c',BOOTSTRAP],input=json.dumps({'root':str(root),'kwargs':kwargs}),
        capture_output=True,text=True,encoding='utf8',check=False)
    require(process.returncode==0,'Archived runtime failed: '+process.stderr[-3000:])
    return json.loads(process.stdout)
