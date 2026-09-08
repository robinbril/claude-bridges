import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BASH = str(Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'Git/bin/bash.exe') if os.name == 'nt' else shutil.which('bash')


@unittest.skipUnless(BASH and Path(BASH).is_file(), 'Git Bash or Bash is required')
class ShellTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.prompt = self.directory / 'prompt.txt'
        self.prompt.write_text('Inspect only.', encoding='utf-8')
        self.context = self.directory / 'context with spaces.txt'
        self.context.write_text('Keep the public contract.', encoding='utf-8')
        self.conf = self.directory / 'delegate.conf'
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(('DELEGATE_', 'CODEX_', 'SENSITIVE_'))}
        self.env.update(DELEGATE_CONF=str(self.conf), DELEGATE_PYTHON=sys.executable,
                        DELEGATE_DRY='1', DELEGATE_CWD=str(self.directory))

    def invoke(self, tools='Read'):
        return subprocess.run([BASH, str(ROOT / 'delegate/delegate.sh'), 'codex', tools, str(self.prompt)],
                              env=self.env, text=True, capture_output=True, timeout=10)

    def test_config_model_and_windows_context_paths_reach_python(self):
        self.conf.write_text('CODEX_MODEL="fixture-model"\n', encoding='utf-8')
        self.env['DELEGATE_CONTEXT'] = str(self.context) + '\n' + str(self.context)
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        contract = json.loads(result.stdout)['contract']
        self.assertEqual(contract['model'], 'fixture-model')
        self.assertEqual(len(contract['context']), 2)
        self.assertEqual(Path(contract['context'][1]['path']), self.context)

    def test_context_callback_and_tools_are_not_shell_reinterpreted(self):
        self.conf.write_text('CODEX_MODEL="fixture-model"\ndelegate_context() {\nprintf "%s\\n" "' +
            self.context.as_posix() + '"\n}\n', encoding='utf-8')
        tools = 'Read Bash(echo "quoted value")'
        result = self.invoke(tools)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['contract']['tools'], tools)
        self.assertEqual(len(json.loads(result.stdout)['contract']['context']), 2)

    def test_failed_context_callback_stops_wrapper(self):
        self.conf.write_text('CODEX_MODEL="fixture-model"\ndelegate_context() { return 7; }\n')
        result = self.invoke()
        self.assertEqual(result.returncode, 7)
        self.assertEqual(result.stdout, '')


if __name__ == '__main__':
    unittest.main()
