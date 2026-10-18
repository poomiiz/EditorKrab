"""Owned render workers stop before timeout/error returns; unrelated processes survive."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import psutil

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
import render_process


class RenderProcessTests(unittest.TestCase):
    def test_completed_process_and_checked_error_keep_output(self):
        command = [sys.executable, "-u", "-c", "import sys;print('hello');print('details',file=sys.stderr)"]
        result = render_process.run(command, capture_output=True, text=True, timeout=10)
        self.assertIsInstance(result, subprocess.CompletedProcess)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "hello")
        self.assertEqual(result.stderr.strip(), "details")
        command[-1] += ";sys.exit(3)"
        with self.assertRaises(subprocess.CalledProcessError) as found:
            render_process.run(command, capture_output=True, text=True, timeout=10, check=True)
        self.assertEqual(found.exception.returncode, 3)
        self.assertEqual(found.exception.stdout.strip(), "hello")

    def _alive(self, pid):
        try:
            return psutil.Process(pid).is_running() and psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
        except psutil.NoSuchProcess:
            return False

    def _tree(self, folder, exit_parent, close_pipes=False):
        marker = Path(folder) / "child.pid"
        worker = Path(folder) / "owned_worker.py"
        worker.write_text("import time\ntime.sleep(120)\n", encoding="utf-8")
        pipes = ",stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL" if close_pipes else ""
        script = ("import subprocess,sys,time;from pathlib import Path;"
                  f"child=subprocess.Popen([sys.executable,{str(worker)!r}]{pipes});"
                  f"Path({str(marker)!r}).write_text(str(child.pid));"
                  "print('render started',flush=True);print('render diagnostic',file=sys.stderr,flush=True);"
                  + ("sys.exit(0)" if exit_parent else "time.sleep(120)"))
        try:
            return render_process.run([sys.executable, "-u", "-c", script],
                                      capture_output=True, text=True, timeout=2)
        finally:
            if marker.is_file():
                pid = int(marker.read_text())
                try:
                    self.assertFalse(self._alive(pid), f"owned render child survived: {pid}")
                finally:
                    if self._alive(pid):
                        proc = psutil.Process(pid)
                        if str(worker) in proc.cmdline():
                            proc.kill()
                            proc.wait(timeout=10)

    def test_timeout_stops_live_parent_tree_and_preserves_diagnostics(self):
        with tempfile.TemporaryDirectory() as folder, self.assertRaises(subprocess.TimeoutExpired) as found:
            self._tree(folder, exit_parent=False)
        self.assertIn("render started", found.exception.output)
        self.assertIn("render diagnostic", found.exception.stderr)

    def test_timeout_stops_orphan_workers_and_leaves_unrelated_process_alive(self):
        unrelated = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(120)"])
        try:
            with tempfile.TemporaryDirectory() as folder, self.assertRaises(subprocess.TimeoutExpired):
                self._tree(folder, exit_parent=True)
            self.assertIsNone(unrelated.poll())
        finally:
            unrelated.kill()
            unrelated.wait(timeout=10)

    def test_normal_exit_also_releases_owned_workers(self):
        with tempfile.TemporaryDirectory() as folder:
            result = self._tree(folder, exit_parent=True, close_pipes=True)
            self.assertEqual(result.returncode, 0)

    @unittest.skipUnless(os.name == "nt", "Windows suspended Job assignment")
    def test_assignment_failure_never_executes_or_leaves_suspended_process(self):
        with tempfile.TemporaryDirectory() as folder:
            marker = Path(folder) / "must-not-execute"
            created = []
            original = subprocess.Popen
            def launch(*args, **kwargs):
                proc = original(*args, **kwargs)
                created.append(proc)
                return proc
            with patch.object(render_process._WindowsJob, "assign", side_effect=RuntimeError("assignment failed")), \
                    patch.object(subprocess, "Popen", side_effect=launch):
                with self.assertRaisesRegex(RuntimeError, "assignment failed"):
                    render_process.run([sys.executable, "-c", f"from pathlib import Path;Path({str(marker)!r}).touch()"],
                                       capture_output=True, text=True, timeout=10)
            self.assertFalse(marker.exists())
            self.assertEqual(len(created), 1)
            self.assertIsNotNone(created[0].poll())


if __name__ == "__main__":
    unittest.main()
