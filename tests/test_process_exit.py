"""Termination tests run isolated processes and never load project data."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import pytest


BACKEND = str(Path(__file__).resolve().parents[1] / 'backend')


def test_immediate_exit_preserves_the_requested_exit_code():
    code = (f'import sys;sys.path.insert(0,{BACKEND!r});'
            'from app.process_exit import exit_immediately;exit_immediately(73)')
    process = subprocess.run([sys.executable, '-c', code], timeout=5,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert process.returncode == 73


@pytest.mark.skipif(os.name != 'nt' or importlib.util.find_spec('numpy') is None,
                    reason='Windows local numerical model runtime')
def test_immediate_exit_terminates_a_worker_blocked_importing_a_model_dll():
    # Holding the CRT stdin lock while NumPy/OpenBLAS loads reproduces the
    # production import deadlock. The watchdog must exit even while it holds.
    paths = [BACKEND, *sys.path]
    code = ('import sys,threading,time;'
            f'sys.path[:0]={paths!r};'
            'from app.process_exit import exit_immediately;'
            'threading.Thread(target=lambda:sys.stdin.buffer.read(1),daemon=True).start();'
            'threading.Timer(.5,lambda:exit_immediately(73)).start();'
            'print("importing local numerical runtime",flush=True);'
            'import numpy;time.sleep(30)')
    with tempfile.TemporaryFile() as output:
        process = subprocess.Popen([sys.executable, '-X', 'utf8', '-c', code],
                                   stdin=subprocess.PIPE, stdout=output, stderr=output,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            process.wait(timeout=6)
            assert process.returncode == 73
            output.seek(0)
            assert b'importing local numerical runtime' in output.read()
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            process.stdin.close()
