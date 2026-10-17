"""Synthetic single-message Responses events with explicit wire identities."""
from copy import deepcopy


def single_message_events(events):
    """Expand terse legacy test data, never provider data, into an identified fixture."""
    events = deepcopy(events)
    text = ''.join(e['delta'] for e in events if e.get('type') == 'response.output_text.delta')
    for event in events:
        if event.get('type') == 'response.output_text.delta':
            event.update(output_index=0, item_id='msg_fixture', content_index=0)
        response = event.get('response', {})
        if event.get('type') == 'response.completed':
            response.setdefault('output', [{'id': 'msg_fixture', 'type': 'message',
                'role': 'assistant', 'status': 'completed',
                'content': [{'type': 'output_text', 'text': text}]}])
            for i, item in enumerate(response['output']):
                item.setdefault('id', 'msg_fixture' if i == 0 else f'item_fixture_{i}')
                if item['type'] == 'message':
                    item.setdefault('role', 'assistant')
                    item.setdefault('status', 'completed')
    return events
