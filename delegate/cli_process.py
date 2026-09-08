"""Run one CLI process tree, then validate its structured completion record."""
import json
import os
import signal
import subprocess
import time


def stop_tree(process):
    if os.name == 'nt':
        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       creationflags=subprocess.CREATE_NO_WINDOW, timeout=10)
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait(timeout=10)


def execute(command, env, cwd, prompt, directory, seconds, output_limit):
    input_file = directory / 'input.txt'
    input_file.write_text(prompt, encoding='utf-8')
    flags = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {'start_new_session': True}
    started = time.monotonic()
    reason = None
    with input_file.open('rb') as source, (directory / 'output.ndjson').open('wb') as output, \
            (directory / 'stderr.txt').open('wb') as errors:
        process = subprocess.Popen(command, cwd=cwd, env=env, stdin=source,
                                   stdout=output, stderr=errors, **flags)
        try:
            while process.poll() is None:
                if time.monotonic() - started >= seconds:
                    reason = 'time_limit'
                elif os.fstat(output.fileno()).st_size + os.fstat(errors.fileno()).st_size > output_limit:
                    reason = 'output_limit'
                if reason:
                    stop_tree(process)
                    break
                time.sleep(0.05)
        except KeyboardInterrupt:
            reason = 'interrupted'
            stop_tree(process)
        finally:
            if process.poll() is None:
                stop_tree(process)
        if os.fstat(output.fileno()).st_size + os.fstat(errors.fileno()).st_size > output_limit:
            reason = reason or 'output_limit'
    return process.returncode, reason, round(time.monotonic() - started, 3)


def parse_output(path, session_id, limit):
    result, initialized = None, False
    problem = None
    if path.stat().st_size > limit:
        return None, False, 'output_limit', session_id
    with path.open('r', encoding='utf-8', errors='replace') as stream:
        for line in stream:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                problem = 'invalid_protocol'
                continue
            if not isinstance(event, dict):
                problem = 'invalid_protocol'
                continue
            # Subagent completions are not the parent task's completion.
            if event.get('parent_tool_use_id'):
                continue
            if session_id is None and event.get('type') in ('system', 'result'):
                candidate = event.get('session_id')
                if isinstance(candidate, str) and candidate:
                    session_id = candidate
            if event.get('session_id') not in (None, session_id):
                problem = 'session_mismatch'
                continue
            if event.get('type') == 'system' and event.get('subtype') == 'init':
                initialized = event.get('session_id') == session_id
            if event.get('type') == 'result':
                if result is not None or event.get('session_id') != session_id:
                    problem = 'invalid_completion'
                result = event
    return result, initialized, problem, session_id


def classify(exit_code, reason, result, protocol_problem):
    if reason or protocol_problem:
        return reason or protocol_problem
    if not result:
        return 'missing_completion'
    if exit_code != 0 or result.get('is_error') is not False or result.get('subtype') != 'success':
        return 'cli_error'
    if not isinstance(result.get('result'), str) or not result['result'].strip():
        return 'empty_completion'
    if result.get('stop_reason') in ('max_tokens', 'refusal', 'tool_deferred'):
        return 'incomplete_completion'
    if result.get('terminal_reason') not in (None, 'completed') or result.get('permission_denials'):
        return 'incomplete_completion'
    return 'completed'
