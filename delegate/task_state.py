"""Local task receipts. No prompt or credentials belong in the manifest."""
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re


def digest(data):
    return hashlib.sha256(data).hexdigest()


def save(path, value):
    temporary = path.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8') as output:
        json.dump(value, output, ensure_ascii=False, indent=2)
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, path)


@contextlib.contextmanager
def task_lock(directory):
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (directory / 'lock').open('a+b') as handle:
        handle.seek(0)
        if not handle.read(1):
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise ValueError('Deze taak is al actief.') from error
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def task_directory(root, task_id):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', task_id):
        raise ValueError('Taak-ID: alleen letters, cijfers, _ en -, maximaal 80 tekens.')
    return root.resolve() / task_id


def positive_integer(env, name, default):
    value = env.get(name, str(default))
    if not re.fullmatch(r'[1-9][0-9]*', value):
        raise ValueError(f'{name} moet een positief geheel getal zijn.')
    return int(value)


def context_prompt(prompt_file, context_files, limit):
    files = [prompt_file, *context_files]
    contents, receipts, seen = [], [], set()
    total = 0
    for filename in files:
        path = Path(filename).expanduser().resolve(strict=True)
        if not path.is_file() or path.stat().st_size > limit:
            raise ValueError(f'Context ontbreekt of is groter dan {limit} bytes: {path}')
        with path.open('rb') as source:
            data = source.read(limit + 1)
        if len(data) > limit:
            raise ValueError(f'Context overschrijdt {limit} bytes: {path}')
        fingerprint = digest(data)
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        text = data.decode('utf-8-sig')
        block = text if not contents else f'\n\n<context source={json.dumps(path.name)}>\n{text}\n</context>'
        total += len(block.encode('utf-8'))
        if total > limit:
            raise ValueError(f'Opdracht plus context overschrijdt {limit} bytes; selecteer minder context.')
        contents.append(block)
        receipts.append({'path': str(path), 'sha256': fingerprint, 'bytes': len(data)})
    if not contents or not contents[0].strip():
        raise ValueError('De opdracht is leeg.')
    return ''.join(contents), receipts


TOKEN_FIELDS = ('input_tokens', 'output_tokens', 'cache_read_input_tokens',
                'cache_creation_input_tokens')


def usage_receipt(result):
    models = result.get('modelUsage') if result else None
    if isinstance(models, dict) and models:
        fields = ('inputTokens', 'outputTokens', 'cacheReadInputTokens', 'cacheCreationInputTokens')
        per_model = {}
        for name, record in models.items():
            record = record if isinstance(record, dict) else {}
            per_model[name] = {key: record.get(field) if type(record.get(field)) is int
                               and record[field] >= 0 else None for key, field in zip(TOKEN_FIELDS, fields)}
        usage = {key: sum(row[key] for row in per_model.values())
                 if all(row[key] is not None for row in per_model.values()) else None for key in TOKEN_FIELDS}
        complete = all(value is not None for value in usage.values())
        if result.get('subtype') == 'error_during_execution' and not any(usage.values()):
            complete = False
        return {**usage, 'complete': complete, 'scope': 'model_tree', 'models': per_model}
    raw = result.get('usage') if result else None
    raw = raw if isinstance(raw, dict) else {}
    usage = {key: raw.get(key) for key in TOKEN_FIELDS}
    for key, value in usage.items():
        if type(value) is not int or value < 0:
            usage[key] = None
    # Top-level usage excludes nested agents. It is a partial receipt, not a task total.
    return {**usage, 'complete': False, 'scope': 'main_loop_only', 'models': {}}


def total_usage(attempts):
    complete = all(item.get('usage', {}).get('complete', False) for item in attempts)
    totals = {key: sum(item.get('usage', {}).get(key) or 0 for item in attempts)
              for key in TOKEN_FIELDS}
    return {'reported': totals, 'complete': complete,
            'total_tokens': sum(totals.values()) if complete else None}
