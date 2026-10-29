"""Explicitly synthetic unresolved claims, never model efficacy or story discoveries."""


def unresolved(text, ids):
    return {'text':text,'relation':'none','a_ids':ids['A'][:1],'b_ids':ids['B'][:1],
            'grade':'U','support':'UNDETERMINED','increment_a_from_b':'','increment_b_from_a':'',
            'reason':'MOCK_NOT_MODEL_OUTPUT; 仅验证逐命题链路',
            'boundary':'不认证任何身份、因果、史实、作者意图或剧情关系',
            'critical_error':None,'evidence_gap':'未进行真实模型语义分析及独立人工核验',
            'story':{'status':'UNKNOWN','bridge':'MOCK没有判断双方是否存在具体剧情桥梁'}}


def unresolved_review(original, ids):
    return unresolved(original['text'],ids) | {'candidate_id':original['id'],'role':original['role']}
