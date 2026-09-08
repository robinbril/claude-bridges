import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'delegate'))
from run_task import command_for


class CommandTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'nt', 'Windows PowerShell launcher')
    def test_cursor_powershell_launcher_does_not_depend_on_pathext(self):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / 'cursor-agent.ps1'
            script.write_text('# fixture; never executed')
            with patch('run_task.shutil.which', return_value=None), patch('run_task.os.get_exec_path', return_value=[directory]):
                command = command_for({'rail': 'cursor', 'model': 'chosen'}, None, False)
            self.assertEqual(command[:5], ['powershell.exe', '-NoProfile', '-NonInteractive', '-File', str(script)])
            self.assertIn('chosen', command)

    def test_claude_batch_launcher_is_not_executed(self):
        with patch('run_task.shutil.which', return_value='C:/fixture/claude.cmd'):
            with self.assertRaisesRegex(ValueError, 'native Claude'):
                command_for({'rail': 'codex'}, 'session', False)


if __name__ == '__main__':
    unittest.main()
