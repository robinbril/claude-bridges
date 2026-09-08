import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'delegate'))
from run_task import check_model, configuration
from task_state import context_prompt, task_lock, total_usage, usage_receipt

DRIVER = '''
import sys
sys.path.insert(0, sys.argv[1])
import run_task
fake = sys.argv[2]
sys.argv = ['run_task', *sys.argv[3:]]
original = run_task.command_for
run_task.shutil.which = lambda _: sys.executable
run_task.command_for = lambda *args: [sys.executable, fake, *original(*args)[1:]]
try:
    sys.exit(run_task.main())
except (ValueError, OSError) as error:
    print(error, file=sys.stderr)
    sys.exit(2)
'''


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.prompt = self.directory / 'prompt.txt'
        self.prompt.write_text('Inspect the fixture.', encoding='utf-8')
        self.calls = self.directory / 'calls.jsonl'
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(('DELEGATE_', 'SEAT_', 'ANTHROPIC_', 'CLAUDE_', 'SENSITIVE_'))}
        self.env.update(SEAT_TEST='-', DELEGATE_TASK_ID='sample',
            DELEGATE_STATE_DIR=str(self.directory / 'state'), DELEGATE_CWD=str(self.directory),
            FAKE_CALLS=str(self.calls), PYTHONIOENCODING='utf-8')

    def command(self, rail='test', model='chosen-model', extra=()):
        return [sys.executable, '-c', DRIVER, str(ROOT / 'delegate'),
                str(ROOT / 'tests' / 'fake_claude.py'), rail, '' if rail == 'cursor' else 'Read Grep', str(self.prompt), model, *extra]

    def run_cli(self, mode='success', extra=(), **env):
        return subprocess.run(self.command(extra=extra), env={**self.env, 'FAKE_MODE': mode, **env},
                              capture_output=True, text=True, encoding='utf-8', timeout=20)

    def state(self):
        return json.loads((self.directory / 'state/sample/task.json').read_text())

    def call_records(self):
        return [json.loads(line) for line in self.calls.read_text().splitlines()]

    def test_success_literal_error_text_does_not_restart(self):
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.call_records()), 1)
        self.assertEqual(self.state()['status'], 'completed')
        self.assertEqual(self.state()['verification'], 'not_run')
        self.assertEqual(self.state()['usage']['total_tokens'], 60)

    def test_zero_exit_without_completion_is_failure_and_never_retries(self):
        result = self.run_cli('drop')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.state()['status'], 'missing_completion')
        self.assertEqual(len(self.call_records()), 1)
        self.assertIsNone(self.state()['usage']['total_tokens'])

    def test_resume_same_session_with_compact_prompt(self):
        self.run_cli('drop')
        result = self.run_cli(DELEGATE_RESUME='1')
        self.assertEqual(result.returncode, 0, result.stderr)
        first, second = self.call_records()
        session = first['args'][first['args'].index('--session-id') + 1]
        self.assertEqual(second['args'][second['args'].index('--resume') + 1], session)
        self.assertNotIn('Inspect the fixture.', second['prompt'])
        self.assertEqual(len(self.state()['attempts']), 2)

    def test_same_task_requires_explicit_resume(self):
        self.run_cli('drop')
        self.assertEqual(self.run_cli().returncode, 2)
        self.assertEqual(len(self.call_records()), 1)

    def test_completed_task_cannot_restart(self):
        self.run_cli()
        self.assertEqual(self.run_cli(DELEGATE_RESUME='1').returncode, 2)
        self.assertEqual(len(self.call_records()), 1)

    def test_changed_prompt_blocks_resume(self):
        self.run_cli('drop')
        self.prompt.write_text('Different task')
        self.assertEqual(self.run_cli(DELEGATE_RESUME='1').returncode, 2)
        self.assertEqual(len(self.call_records()), 1)

    def test_attempt_budget_covers_all_resumes(self):
        self.run_cli('error', DELEGATE_MAX_ATTEMPTS='1')
        self.assertEqual(self.run_cli(DELEGATE_MAX_ATTEMPTS='1', DELEGATE_RESUME='1').returncode, 2)
        self.assertEqual(len(self.call_records()), 1)

    def test_time_budget_stops_process_and_blocks_resume(self):
        result = self.run_cli('sleep', DELEGATE_MAX_SECONDS='1')
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(self.state()['status'], 'time_limit')
        self.assertEqual(self.run_cli(DELEGATE_MAX_SECONDS='1', DELEGATE_RESUME='1').returncode, 2)

    def test_output_budget_stops_process(self):
        result = self.run_cli('large', DELEGATE_MAX_OUTPUT_BYTES='1024')
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(self.state()['status'], 'output_limit')

    def test_fast_stderr_flood_cannot_report_success(self):
        self.assertEqual(self.run_cli('stderr_flood', DELEGATE_MAX_OUTPUT_BYTES='1024').returncode, 1)
        self.assertEqual(self.state()['status'], 'output_limit')

    def test_native_seat_does_not_inherit_bridge_credentials(self):
        result = self.run_cli(ANTHROPIC_BASE_URL='http://wrong', ANTHROPIC_API_KEY='fixture-key',
                              ANTHROPIC_SMALL_FAST_MODEL='wrong')
        self.assertEqual(result.returncode, 0, result.stderr)
        call = self.call_records()[0]
        self.assertIsNone(call['base'])
        self.assertIsNone(call['api_key'])
        self.assertIsNone(call['small_model'])

    def test_false_mcp_flag_still_enforces_strict_mode(self):
        self.run_cli(DELEGATE_MCP='0')
        self.assertIn('--strict-mcp-config', self.call_records()[0]['args'])

    def test_missing_usage_is_unknown_not_zero(self):
        self.run_cli('missing_usage')
        self.assertIsNone(self.state()['usage']['total_tokens'])
        self.assertFalse(self.state()['usage']['complete'])

    def test_protocol_and_incomplete_results_fail_closed(self):
        for mode in ('invalid', 'mismatch', 'duplicate', 'truncated', 'denied', 'error'):
            with self.subTest(mode=mode):
                result = self.run_cli(mode, DELEGATE_TASK_ID=mode)
                self.assertEqual(result.returncode, 1, result.stderr)

    def test_missing_context_stops_before_cli(self):
        result = self.run_cli(extra=('--context', str(self.directory / 'absent.txt')))
        self.assertEqual(result.returncode, 2)
        self.assertFalse(self.calls.exists())

    def test_auto_is_rejected_before_cli(self):
        result = subprocess.run(self.command(rail='auto'), env=self.env, capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertFalse(self.calls.exists())

    def test_empty_or_large_prompt_stops_before_cli(self):
        for content in ('', 'x' * 100):
            self.prompt.write_text(content)
            self.assertEqual(self.run_cli(DELEGATE_MAX_INPUT_BYTES='50').returncode, 2)
        self.assertFalse(self.calls.exists())

    def test_context_is_deduplicated_by_content(self):
        other = self.directory / 'same file.txt'
        other.write_text(self.prompt.read_text())
        prompt, receipts = context_prompt(self.prompt, [str(other), str(other)], 1000)
        self.assertEqual(prompt, self.prompt.read_text())
        self.assertEqual(len(receipts), 1)

    def test_task_id_cannot_escape_state_directory(self):
        self.assertEqual(self.run_cli(DELEGATE_TASK_ID='../escape').returncode, 2)
        self.assertFalse(self.calls.exists())

    def test_exclusive_task_lock_prevents_duplicate_launch(self):
        with task_lock(self.directory / 'state/sample'):
            result = self.run_cli()
        self.assertEqual(result.returncode, 2)
        self.assertFalse(self.calls.exists())

    def test_invalid_limit_is_rejected(self):
        self.assertEqual(self.run_cli(DELEGATE_MAX_ATTEMPTS='0').returncode, 2)
        self.assertFalse(self.calls.exists())

    def test_cursor_keeps_native_runner_and_resumes_observed_session(self):
        first = subprocess.run(self.command(rail='cursor'), env={**self.env, 'FAKE_MODE': 'drop'}, capture_output=True)
        self.assertEqual(first.returncode, 1)
        self.assertEqual(self.state()['session_id'], 'cursor-fixture-session')
        second = subprocess.run(self.command(rail='cursor'), env={**self.env, 'DELEGATE_RESUME': '1'}, capture_output=True)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn('--resume', self.call_records()[1]['args'])

    def test_reported_token_threshold_blocks_additional_attempt(self):
        self.run_cli('error', DELEGATE_MAX_REPORTED_TOKENS='50')
        result = self.run_cli(DELEGATE_MAX_REPORTED_TOKENS='50', DELEGATE_RESUME='1')
        self.assertEqual(result.returncode, 2)
        self.assertEqual(len(self.call_records()), 1)

    def test_reported_token_budget_blocks_when_prior_usage_unknown(self):
        self.run_cli('drop', DELEGATE_MAX_REPORTED_TOKENS='1000')
        result = self.run_cli(DELEGATE_MAX_REPORTED_TOKENS='1000', DELEGATE_RESUME='1')
        self.assertEqual(result.returncode, 2)
        self.assertEqual(len(self.call_records()), 1)


if __name__ == '__main__':
    unittest.main()
