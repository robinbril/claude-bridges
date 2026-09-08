"""Explicit, bounded Claude Code tasks; retries are always operator initiated."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import uuid

from cli_process import classify, execute, parse_output
from task_state import (context_prompt, digest, positive_integer, save,
                        task_directory, task_lock, total_usage, usage_receipt)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, url):
        return None


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('rail')
    parser.add_argument('tools')
    parser.add_argument('prompt_file')
    parser.add_argument('model', nargs='?')
    parser.add_argument('--context', action='append', default=[])
    return parser.parse_args()


def configuration(args, env):
    if args.rail == 'auto':
        raise ValueError('Kies een expliciete rail en model; automatische verdeling is verwijderd.')
    if not re.fullmatch(r'[A-Za-z0-9_-]+', args.rail):
        raise ValueError('Ongeldige rail.')
    bridge = args.rail in ('grok', 'codex', 'openrouter')
    model = args.model or env.get(f'{args.rail.upper()}_MODEL')
    if not model or model.startswith('-') or any(c.isspace() for c in model):
        raise ValueError('Kies exact een model via het vierde argument of <RAIL>_MODEL.')
    effort = env.get('DELEGATE_EFFORT', env.get(f'{args.rail.upper()}_EFFORT', ''))
    if effort and effort not in ('low', 'medium', 'high', 'xhigh', 'max'):
        raise ValueError('Ongeldige effort.')
    config_dir = env.get('BRIDGE_CONFIG_DIR', str(Path.home() / '.claude-bridge')) if bridge else env.get('SEAT_' + args.rail.upper().replace('-', '_'))
    if args.rail == 'cursor':
        config_dir = ''
        if args.tools or effort:
            raise ValueError('Cursor gebruikt eigen toolrechten; geef lege tools en geen DELEGATE_EFFORT.')
    if not bridge and args.rail != 'cursor' and not config_dir:
        raise ValueError('Native seat ontbreekt in de configuratie.')
    if config_dir == '-':
        config_dir = env.get('CLAUDE_CONFIG_DIR', '')
    mcp = env.get('DELEGATE_MCP', '0')
    if mcp not in ('0', '1'):
        raise ValueError('DELEGATE_MCP moet 0 of 1 zijn.')
    return {'rail': args.rail, 'model': model, 'effort': effort, 'tools': args.tools,
            'bridge_url': env.get('BRIDGE_URL', 'http://127.0.0.1:8317') if bridge else None,
            'config_dir': str(Path(config_dir).expanduser().resolve()) if config_dir else str(Path.home() / '.claude'),
            'mcp': mcp, 'cwd': str(Path(env.get('DELEGATE_CWD', os.getcwd())).resolve())}


def child_environment(config, env):
    child = dict(env)
    # Never let stale routing or API credentials leak into a native seat.
    for name in ('ANTHROPIC_BASE_URL', 'ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN',
                 'ANTHROPIC_MODEL', 'ANTHROPIC_SMALL_FAST_MODEL', 'ANTHROPIC_DEFAULT_OPUS_MODEL',
                 'ANTHROPIC_DEFAULT_SONNET_MODEL', 'ANTHROPIC_DEFAULT_HAIKU_MODEL',
                 'CLAUDE_CODE_USE_BEDROCK', 'CLAUDE_CODE_USE_VERTEX', 'CLAUDE_CODE_USE_FOUNDRY'):
        child.pop(name, None)
    child['CLAUDE_CONFIG_DIR'] = config['config_dir']
    child['DISABLE_AUTOUPDATER'] = '1'
    child['CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC'] = '1'
    if config['bridge_url']:
        if not env.get('MODEL_ROUTER_KEY'):
            raise ValueError('MODEL_ROUTER_KEY ontbreekt.')
        child['ANTHROPIC_BASE_URL'] = config['bridge_url']
        child['ANTHROPIC_API_KEY'] = env['MODEL_ROUTER_KEY']
        for key in ('ANTHROPIC_SMALL_FAST_MODEL', 'ANTHROPIC_DEFAULT_OPUS_MODEL',
                    'ANTHROPIC_DEFAULT_SONNET_MODEL', 'ANTHROPIC_DEFAULT_HAIKU_MODEL'):
            child[key] = config['model']
    return child


def command_for(config, session, resume):
    if config['rail'] == 'cursor':
        executable = shutil.which('cursor-agent')
        if os.name == 'nt' and (not executable or Path(executable).suffix.lower() in ('.cmd', '.bat')):
            script = next((str(Path(folder) / 'cursor-agent.ps1') for folder in os.get_exec_path()
                           if (Path(folder) / 'cursor-agent.ps1').is_file()), None)
            if not script:
                raise ValueError('Native cursor-agent executable of PowerShell launcher ontbreekt.')
            prefix = ['powershell.exe', '-NoProfile', '-NonInteractive', '-File', script]
        elif executable:
            prefix = [executable]
        else:
            raise ValueError('Native cursor-agent ontbreekt in PATH.')
        return prefix + ['-p', '--model', config['model'], '--output-format', 'stream-json'] + (['--resume', session] if resume else [])
    executable = shutil.which('claude')
    if not executable:
        raise ValueError('Claude CLI ontbreekt in PATH.')
    if Path(executable).suffix.lower() in ('.cmd', '.bat'):
        raise ValueError('Gebruik de native Claude executable; batch-launchers worden niet uitgevoerd.')
    command = [executable, '-p', '--model', config['model'], '--allowedTools', config['tools'],
               '--output-format', 'stream-json', '--verbose', '--permission-mode', 'dontAsk',
               '--resume' if resume else '--session-id', session]
    if config['effort']:
        command += ['--effort', config['effort']]
    if config['mcp'] == '0':
        command.append('--strict-mcp-config')
    return command


def check_model(config, env):
    if not config['bridge_url']:
        return
    parsed = urlsplit(config['bridge_url'])
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.query or parsed.fragment:
        raise ValueError('BRIDGE_URL moet een HTTP(S) basis-URL zonder credentials/query zijn.')
    base = config['bridge_url'].rstrip('/')
    url = base + ('/models' if base.endswith('/v1') else '/v1/models')
    request = Request(url, headers={'Authorization': 'Bearer ' + env['MODEL_ROUTER_KEY']})
    try:
        with build_opener(NoRedirect).open(request, timeout=5) as response:
            payload = json.loads(response.read(1048577))
    except (HTTPError, URLError, TimeoutError, ValueError) as error:
        raise ValueError('Modelcatalogus niet bereikbaar of ongeldig; geen agent gestart.') from error
    models = payload.get('data') if isinstance(payload, dict) else None
    if not isinstance(models, list) or not any(isinstance(item, dict) and item.get('id') == config['model'] for item in models):
        raise ValueError('Gekozen model ontbreekt in de bridgecatalogus; geen agent gestart.')


def privacy_check(prompt, config, env):
    external = config['rail'] in ('grok', 'codex', 'openrouter', 'cursor')
    if env.get('DELEGATE_SENSITIVE') not in (None, '0', '1'):
        raise ValueError('DELEGATE_SENSITIVE moet 0 of 1 zijn.')
    sensitive = env.get('DELEGATE_SENSITIVE') == '1'
    if env.get('SENSITIVE_RE'):
        sensitive = sensitive or bool(re.search(env['SENSITIVE_RE'], prompt, re.IGNORECASE))
    if sensitive and external:
        raise ValueError('Gevoelige opdracht/context vereist een native seat.')


def limits_from(env):
    return {'attempts': positive_integer(env, 'DELEGATE_MAX_ATTEMPTS', 3),
            'seconds': positive_integer(env, 'DELEGATE_MAX_SECONDS', 900),
            'input_bytes': positive_integer(env, 'DELEGATE_MAX_INPUT_BYTES', 65536),
            'output_bytes': positive_integer(env, 'DELEGATE_MAX_OUTPUT_BYTES', 16777216),
            'reported_tokens': positive_integer(env, 'DELEGATE_MAX_REPORTED_TOKENS', 1)
                if 'DELEGATE_MAX_REPORTED_TOKENS' in env else None}


def prepare_state(path, contract, limits, resume):
    if not path.exists():
        if resume:
            raise ValueError('Hervatten vereist een bestaande taak-ID.')
        return {'schema_version': 1, 'contract': contract, 'limits': limits,
                'session_id': None if contract['rail'] == 'cursor' else str(uuid.uuid4()), 'session_confirmed': False,
                'status': 'created', 'attempts': [], 'verification': 'not_run'}
    if not resume:
        raise ValueError('Taak bestaat al; gebruik DELEGATE_RESUME=1 om bewust te hervatten.')
    state = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(state, dict) or state.get('schema_version') != 1:
        raise ValueError('Onbekend taakdossierformaat; geen agent gestart.')
    if state['contract'] != contract or state['limits'] != limits:
        raise ValueError('Model, tools, context, werkomgeving of limieten gewijzigd; hervatten geblokkeerd.')
    if state['status'] in ('running', 'completed'):
        raise ValueError('Taak is actief/onderbroken zonder afsluiting of al voltooid; inspecteer het taakdossier.')
    if state['status'] in ('session_mismatch', 'invalid_protocol', 'invalid_completion'):
        raise ValueError('Sessiebewijs is ongeldig; inspecteer het taakdossier voor een nieuwe opdracht.')
    if not state['session_confirmed']:
        raise ValueError('Geen bevestigde sessie om te hervatten; inspecteer het taakdossier.')
    return state


def workspace_receipt(cwd):
    def git(*args):
        result = subprocess.run(['git', '-C', cwd, *args], capture_output=True, timeout=10)
        return result.stdout.decode('utf-8', errors='replace').strip() if result.returncode == 0 else None
    if not shutil.which('git') or git('rev-parse', '--is-inside-work-tree') != 'true':
        return None
    return {'head': git('rev-parse', 'HEAD'), 'branch': git('symbolic-ref', '--short', '-q', 'HEAD'),
            'status': git('status', '--short', '--untracked-files=normal')}


def continuation_prompt():
    return ('Hervat de bestaande opdracht. Controleer eerst de actuele bestanden en eerdere '
            'toolresultaten. Herhaal geen al uitgevoerde acties. Rond het resterende werk af '
            'binnen de oorspronkelijke eisen en rapporteer bewijs en openstaande punten.')


def run_attempt(state, path, config, child, prompt):
    limits, attempts = state['limits'], state['attempts']
    remaining = limits['seconds'] - sum(item['seconds'] for item in attempts)
    if len(attempts) >= limits['attempts'] or remaining <= 0:
        raise ValueError('Taakbudget opgebruikt; geen nieuwe poging gestart.')
    if attempts and limits['reported_tokens'] is not None:
        used = total_usage(attempts)['total_tokens']
        if used is None or used >= limits['reported_tokens']:
            raise ValueError('Tokenverbruik onbekend of grens bereikt; geen nieuwe poging gestart.')
    resume = bool(attempts)
    prompt = continuation_prompt() if resume else prompt
    if len(prompt.encode('utf-8')) > limits['input_bytes']:
        raise ValueError('Hervatinstructie overschrijdt de invoerlimiet.')
    command = command_for(config, state['session_id'], resume)
    workspace = workspace_receipt(config['cwd'])
    if resume and workspace and state.get('workspace_after'):
        previous = state['workspace_after']
        if (workspace['head'], workspace['branch']) != (previous['head'], previous['branch']):
            raise ValueError('Git-branch of commit gewijzigd; hervatten geblokkeerd.')
    check_model(config, child)
    directory = path.parent / f'attempt-{len(attempts) + 1}'
    directory.mkdir(mode=0o700)
    state['status'] = 'running'
    save(path, state)
    print(f'[delegate] task={path.parent.name} model={config["model"]} attempt={len(attempts) + 1}', file=sys.stderr)
    try:
        rc, reason, elapsed = execute(command, child, config['cwd'], prompt, directory,
                                      remaining, limits['output_bytes'])
        result, initialized, problem, session = parse_output(directory / 'output.ndjson', state['session_id'], limits['output_bytes'])
        state['session_id'] = session
        status = classify(rc, reason, result, problem)
    except OSError:
        rc, elapsed, result, initialized, status = 1, 0, None, False, 'launch_error'
    state['session_confirmed'] = state['session_confirmed'] or initialized or bool(result and result.get('session_id') == state['session_id'])
    attempts.append({'status': status, 'exit_code': rc, 'seconds': elapsed,
                     'usage': usage_receipt(result), 'directory': directory.name})
    state['status'] = status
    state['workspace_before'] = workspace
    state['workspace_after'] = workspace_receipt(config['cwd'])
    state['usage'] = total_usage(attempts)
    state['usage']['reported_limit_exceeded'] = (
        state['usage']['total_tokens'] > limits['reported_tokens']
        if state['usage']['total_tokens'] is not None and limits['reported_tokens'] is not None else None)
    save(path, state)
    if result and isinstance(result.get('result'), str):
        print(result['result'])
    print(f'[delegate] status={status} receipt={path}', file=sys.stderr)
    return 0 if status == 'completed' else 1


def main():
    args, env = arguments(), os.environ
    if env.get('DELEGATE_RESUME', '0') not in ('0', '1'):
        raise ValueError('DELEGATE_RESUME moet 0 of 1 zijn.')
    if env.get('DELEGATE_RESUME') == '1' and not env.get('DELEGATE_TASK_ID'):
        raise ValueError('Hervatten vereist DELEGATE_TASK_ID.')
    config, limits = configuration(args, env), limits_from(env)
    if not Path(config['cwd']).is_dir():
        raise ValueError('DELEGATE_CWD bestaat niet.')
    prompt, receipts = context_prompt(args.prompt_file, args.context, limits['input_bytes'])
    privacy_check(prompt, config, env)
    contract = {**config, 'prompt_sha256': digest(prompt.encode('utf-8')), 'context': receipts}
    if env.get('DELEGATE_DRY') == '1':
        print(json.dumps({'contract': contract, 'limits': limits}, ensure_ascii=False))
        return 0
    child = child_environment(config, env)
    task_id = env.get('DELEGATE_TASK_ID', str(uuid.uuid4()))
    root = Path(env.get('DELEGATE_STATE_DIR', Path(__file__).parent / 'state')).expanduser()
    directory = task_directory(root, task_id)
    with task_lock(directory):
        path = directory / 'task.json'
        state = prepare_state(path, contract, limits, env.get('DELEGATE_RESUME') == '1')
        return run_attempt(state, path, config, child, prompt)


if __name__ == '__main__':
    os.umask(0o077)
    try:
        sys.exit(main())
    except (ValueError, OSError, re.error) as error:
        print(f'[delegate] {error}', file=sys.stderr)
        sys.exit(2)
