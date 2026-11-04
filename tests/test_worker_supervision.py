"""Real process tests use temporary files and never load the project database."""
import ctypes
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest

from app import worker_supervisor as supervisor


BACKEND = str(Path(__file__).resolve().parents[1] / 'backend')


def wait_until(predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.03)
    assert predicate(), 'Child process did not reach the expected state'


def alive(pid):
    if os.name == 'nt':
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.OpenProcess(0x100000, False, pid)
        if not handle:
            return False
        try:
            return kernel.WaitForSingleObject(handle, 0) == 258
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def test_crashed_worker_restarts_and_clean_exit_stops(tmp_path, capsys):
    attempts = tmp_path / 'attempts.txt'
    code = ('from pathlib import Path; import sys; '
            f'p=Path({str(attempts)!r}); '
            'n=int(p.read_text())+1 if p.exists() else 1; '
            'p.write_text(str(n)); sys.exit(3 if n==1 else 0)')
    result = supervisor.supervise([sys.executable, '-c', code], directory=tmp_path, restart_delay=.01)
    assert result == 0
    assert attempts.read_text() == '2'
    assert 'exited (3)' in capsys.readouterr().err


def test_frozen_supervisor_uses_embedded_runner_path(tmp_path,monkeypatch):
    embedded=tmp_path/'_internal';embedded.mkdir()
    monkeypatch.setattr(sys,'_MEIPASS',str(embedded),raising=False)
    commands=[]
    class Child:
        stdin=None
        def wait(self):return 0
        def poll(self):return 0
    def spawn(command,**kwargs):commands.append(command);return Child()
    monkeypatch.setattr(supervisor.subprocess,'Popen',spawn)
    assert supervisor.supervise(directory=tmp_path)==0
    assert Path(commands[0][-2])==embedded/'runner.py'
    assert commands[0][-1]=='worker-child'


def test_killed_supervisor_releases_child_and_prevents_duplicate_worker(tmp_path):
    ready = tmp_path / 'child.pid'
    root = str(Path(__file__).resolve().parents[1])
    child_code = ('import sys, threading, time, os; from pathlib import Path; '
                  f'sys.path.insert(0,{root!r}); import runner; '
                  'from app.worker_supervisor import exit_when_parent_closes; '
                  'threading.Thread(target=exit_when_parent_closes,daemon=True).start(); '
                  'import numpy as np; assert np.dot([1,2],[3,4]) == 11; '
                  f'Path({str(ready)!r}).write_text(str(os.getpid())); '
                  'time.sleep(120)')
    command = [sys.executable, '-c', child_code]
    parent_code = ('import sys; '
                   f'sys.path.insert(0,{BACKEND!r}); '
                   'from app.worker_supervisor import supervise; '
                   f'sys.exit(supervise({command!r},directory={str(tmp_path)!r},restart_delay=.01))')
    parent = subprocess.Popen([sys.executable, '-c', parent_code],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    child_pid = None
    try:
        wait_until(lambda: ready.exists() and ready.stat().st_size)
        child_pid = int(ready.read_text())
        assert alive(child_pid)
        assert supervisor.supervise(command, directory=tmp_path, restart_delay=.01) == 75
        assert parent.poll() is None and alive(child_pid)
        parent.kill()
        parent.wait(timeout=5)
        wait_until(lambda: not alive(child_pid))
        # The crashed supervisor leaves a file; its OS lock is nevertheless free.
        with supervisor.exclusive_lock(tmp_path / '.worker-supervisor.lock'):
            pass
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait(timeout=5)
        if child_pid is not None:
            wait_until(lambda: not alive(child_pid))


def test_execution_lock_blocks_overlap_and_releases_after_error(tmp_path):
    lock = tmp_path / '.worker-execution.lock'
    with pytest.raises(ValueError):
        with supervisor.exclusive_lock(lock):
            with pytest.raises(supervisor.WorkerAlreadyRunning):
                with supervisor.exclusive_lock(lock):
                    pytest.fail('A replacement worker acquired an active execution lock')
            raise ValueError('simulated worker failure')
    with supervisor.exclusive_lock(lock):
        pass


@pytest.mark.skipif(os.name != 'nt', reason='Windows native-library and CRT locking regression')
def test_parent_monitor_allows_real_numpy_import_and_clean_process_exit(tmp_path):
    root = str(Path(__file__).resolve().parents[1])
    ready = tmp_path / 'numpy-ready.json'
    code = (
        'import sys, threading, time, json; from pathlib import Path; '
        f'sys.path.insert(0,{root!r}); import runner; '
        'from app.worker_supervisor import exit_when_parent_closes; '
        'threading.Thread(target=exit_when_parent_closes,daemon=True).start(); '
        'time.sleep(.1); import numpy as np; '
        f'Path({str(ready)!r}).write_text(json.dumps(np.dot([[1,2]],[[3],[4]]).tolist())); '
        'time.sleep(.1)'
    )
    child = subprocess.Popen([sys.executable, '-c', code], stdin=subprocess.PIPE,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        wait_until(lambda: ready.exists() and ready.stat().st_size, timeout=12)
        assert json.loads(ready.read_text()) == [[11]]
        child.wait(timeout=5)
        assert child.returncode == 0
    finally:
        if not child.stdin.closed:
            child.stdin.close()
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)


@pytest.mark.skipif(os.name != 'nt' or not shutil.which('pwsh.exe'), reason='Windows launcher integration')
def test_stop_launcher_ends_supervisor_and_its_owned_queue_child(tmp_path):
    root = Path(__file__).resolve().parents[1]
    stop_script = tmp_path / '停止论文工作台.ps1'
    shutil.copyfile(root / '停止论文工作台.ps1', stop_script)
    ready = tmp_path / 'child.pid'
    runner = tmp_path / 'runner.py'
    runner.write_text(
        'import os, sys, threading, time\nfrom pathlib import Path\n'
        f'sys.path.insert(0,{BACKEND!r})\n'
        'from app.worker_supervisor import supervise, exit_when_parent_closes\n'
        "if sys.argv[1] == 'worker':\n"
        f'    raise SystemExit(supervise([sys.executable,__file__,"worker-child"],directory={str(tmp_path)!r}))\n'
        'threading.Thread(target=exit_when_parent_closes,daemon=True).start()\n'
        f'Path({str(ready)!r}).write_text(str(os.getpid()))\n'
        'time.sleep(120)\n', encoding='utf-8')
    parent = subprocess.Popen([sys.executable, str(runner), 'worker'],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    child_pid = None
    try:
        wait_until(lambda: ready.exists() and ready.stat().st_size)
        child_pid = int(ready.read_text())
        runtime = tmp_path / '.runtime'
        runtime.mkdir()
        (runtime / 'processes-9876.json').write_text(json.dumps({
            'worker_pid': parent.pid, 'server_pid': 0, 'runner': str(runner)}), encoding='utf-8')
        quoted = str(stop_script).replace("'", "''")
        command = ("if ($PSVersionTable.PSVersion -le [version]'7.6.0' -or "
                   "$PSVersionTable.PSEdition -ne 'Core') { throw 'Requires PowerShell > 7.6.0 Core' }; "
                   f"& '{quoted}' -Port 9876")
        result = subprocess.run(['pwsh.exe', '-NoProfile', '-NonInteractive', '-Command', command],
                                capture_output=True, timeout=20)
        assert result.returncode == 0, result.stderr.decode(errors='replace')
        parent.wait(timeout=5)
        wait_until(lambda: not alive(child_pid))
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait(timeout=5)
        if child_pid is not None:
            wait_until(lambda: not alive(child_pid))
