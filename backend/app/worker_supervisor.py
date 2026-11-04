"""Keep the queue worker recoverable without leaving orphan task processes."""
import contextlib
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
from .process_exit import exit_immediately

if os.name == 'nt':
    import ctypes
    from ctypes import wintypes
    _kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    _kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel.OpenProcess.restype = wintypes.HANDLE
    _kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _kernel.WaitForSingleObject.restype = wintypes.DWORD
    _kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel.CloseHandle.restype = wintypes.BOOL


class WorkerAlreadyRunning(RuntimeError):
    pass


@contextlib.contextmanager
def exclusive_lock(path):
    """An OS lock survives a stale lock file and is released after a crash."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open('a+b')
    locked = False
    try:
        if os.name == 'nt':
            import msvcrt
            if not path.stat().st_size:
                stream.write(b'\0')
                stream.flush()
            stream.seek(0)
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as error:
                raise WorkerAlreadyRunning(str(path)) from error
        else:
            import fcntl
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                raise WorkerAlreadyRunning(str(path)) from error
        locked = True
        yield
    finally:
        if locked:
            if os.name == 'nt':
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)
        stream.close()


def _wait_for_parent_process_windows():
    """Observe parent termination without reading Windows standard input.

    A blocked stdin read deadlocks NumPy/OpenBLAS DLL initialization on Windows.
    Waiting on a process handle avoids that standard-input dependency entirely.
    The handle also continues identifying this parent if Windows reuses its PID.
    """
    handle = _kernel.OpenProcess(0x00100000, False, os.getppid())  # SYNCHRONIZE
    if not handle:
        if ctypes.get_last_error() == 87:  # Parent already exited.
            return
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if _kernel.WaitForSingleObject(handle, 0xFFFFFFFF) != 0:
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        _kernel.CloseHandle(handle)


def exit_when_parent_closes(stream=None):
    """End an orphan worker even while its task is blocked in a native DLL."""
    try:
        if os.name == 'nt' and stream is None:
            _wait_for_parent_process_windows()
        else:
            stream = stream if stream is not None else sys.stdin.buffer
            while stream.read(1):
                pass
    finally:
        exit_immediately(0)


def data_directory():
    root = Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parents[2]))
    return Path(os.environ.get('PAPER_DATA_DIR', str(root / 'data'))).resolve()


def run_child():
    threading.Thread(target=exit_when_parent_closes, name='worker-parent-monitor', daemon=True).start()
    diagnostic_interval = float(os.environ.get('PAPER_TASK_TRACEBACK_SECONDS', '0'))
    if diagnostic_interval > 0:
        import faulthandler
        faulthandler.dump_traceback_later(diagnostic_interval, repeat=True)
    try:
        # A replacement cannot claim work before the old parent's child exits.
        with exclusive_lock(data_directory() / '.worker-execution.lock'):
            from .tasks import run_forever
            from .runtime_identity import worker_heartbeat
            with worker_heartbeat():run_forever()
    except WorkerAlreadyRunning:
        return 75
    return 0


def supervise(command=None, directory=None, restart_delay=1.0):
    """Restart failed children; a clean exit or an explicit stop ends the loop."""
    root = Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parents[2]))
    command = command or [sys.executable, '-X', 'utf8', str(root / 'runner.py'), 'worker-child']
    directory = Path(directory) if directory is not None else data_directory()
    stopping = threading.Event()
    child = None
    previous_handlers = {}
    if os.environ.get('PAPER_WORKER_MONITOR_PARENT') == '1':
        threading.Thread(target=exit_when_parent_closes,name='supervisor-parent-monitor',daemon=True).start()

    def stop(_signum, _frame):
        stopping.set()
        if child is not None and child.poll() is None:
            child.terminate()

    try:
        with exclusive_lock(directory / '.worker-supervisor.lock'):
            if threading.current_thread() is threading.main_thread():
                for signum in (signal.SIGTERM, signal.SIGINT):
                    previous_handlers[signum] = signal.signal(signum, stop)
            failures = 0
            while not stopping.is_set():
                started = time.monotonic()
                child = subprocess.Popen(command, stdin=subprocess.DEVNULL if os.name == 'nt' else subprocess.PIPE,
                                         creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                if stopping.is_set() and child.poll() is None:
                    child.terminate()
                code = child.wait()
                if child.stdin is not None:
                    child.stdin.close()
                child = None
                if stopping.is_set() or code == 0:
                    return code
                failures = failures + 1 if time.monotonic() - started < 60 else 1
                delay = min(30.0, restart_delay * failures)
                print(f'Queue worker exited ({code}); restarting in {delay:g}s from saved checkpoints.',
                      file=sys.stderr, flush=True)
                stopping.wait(delay)
    except WorkerAlreadyRunning:
        print('A queue worker supervisor already owns this data directory.', file=sys.stderr, flush=True)
        return 75
    finally:
        # Closing stdin is also effective when termination interrupts a wait.
        if child is not None:
            if child.stdin is not None:
                child.stdin.close()
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
    return 0
