import subprocess
import sys
import unittest
from unittest.mock import patch

from app.media.processing import run_process


class MediaProcessStdinTests(unittest.TestCase):
    def test_child_receives_eof_instead_of_host_control_input(self):
        with patch('app.media.processing.subprocess.Popen', wraps=subprocess.Popen) as popen:
            output = run_process([sys.executable, '-c', 'import sys; print(repr(sys.stdin.read()))'], timeout=5)
        self.assertEqual(output.strip(), "''")
        self.assertEqual(popen.call_args.kwargs['stdin'], subprocess.DEVNULL)
