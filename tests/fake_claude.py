"""A deterministic CLI process. Never contacts a model or reads credentials."""
import json
import os
from pathlib import Path
import sys
import time

args = sys.argv[1:]
session = (args[args.index('--resume' if '--resume' in args else '--session-id') + 1]
           if '--resume' in args or '--session-id' in args else 'cursor-fixture-session')
mode = os.environ.get('FAKE_MODE', 'success')
prompt = sys.stdin.read()
with Path(os.environ['FAKE_CALLS']).open('a', encoding='utf-8') as output:
    output.write(json.dumps({'args': args, 'prompt': prompt,
        'base': os.environ.get('ANTHROPIC_BASE_URL'),
        'api_key': os.environ.get('ANTHROPIC_API_KEY'),
        'small_model': os.environ.get('ANTHROPIC_SMALL_FAST_MODEL')}) + '\n')

def emit(message):
    print(json.dumps({'session_id': session, **message}), flush=True)

emit({'type': 'system', 'subtype': 'init'})
if mode == 'sleep':
    time.sleep(15)
if mode == 'drop':
    emit({'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': 'partial'}]}})
    sys.exit(0)
if mode == 'invalid':
    print('not a protocol message', flush=True)
if mode == 'large':
    print('x' * 10000, flush=True)
    time.sleep(15)
if mode == 'stderr_flood':
    print('x' * 10000, file=sys.stderr, flush=True)
usage = {'input_tokens': 10, 'output_tokens': 20,
         'cache_read_input_tokens': 30, 'cache_creation_input_tokens': 0}
result = {'type': 'result', 'subtype': 'success', 'is_error': False,
          'result': 'Server error mid-response is documented; work completed.', 'usage': usage,
          'modelUsage': {'chosen-model': {'inputTokens': 10, 'outputTokens': 20,
                          'cacheReadInputTokens': 30, 'cacheCreationInputTokens': 0}}}
if mode == 'error':
    result.update(subtype='error_during_execution', is_error=True)
if mode == 'missing_usage':
    result.pop('usage')
    result.pop('modelUsage')
if mode == 'truncated':
    result['stop_reason'] = 'max_tokens'
if mode == 'denied':
    result['permission_denials'] = [{'tool_name': 'Bash'}]
if mode == 'mismatch':
    result['session_id'] = 'wrong'
emit(result)
if mode == 'duplicate':
    emit(result)
