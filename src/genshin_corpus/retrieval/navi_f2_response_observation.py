"""Read-only classification of archived Responses terminal envelopes."""

def classify_terminal(response, model):
    if not isinstance(response, dict) or not response.get('id') or response.get('model') != model:
        return {'state':'UNKNOWN', 'reason':'missing_identity'}
    usage=response.get('usage')
    valid=isinstance(usage,dict) and all(type(usage.get(k)) is int and usage[k]>=0 for k in ('input_tokens','output_tokens','total_tokens')) and usage['input_tokens']+usage['output_tokens']==usage['total_tokens']
    status=response.get('status')
    if status=='incomplete':
        detail=response.get('incomplete_details') or {}
        return {'state':'INCOMPLETE', 'reason':detail.get('reason','UNKNOWN'), 'usage':usage if valid else 'UNKNOWN'}
    if status=='completed': return {'state':'COMPLETED','usage':usage if valid else 'UNKNOWN'}
    if status=='failed': return {'state':'FAILED','usage':usage if valid else 'UNKNOWN'}
    return {'state':'UNKNOWN','reason':'unrecognized_terminal','usage':usage if valid else 'UNKNOWN'}
