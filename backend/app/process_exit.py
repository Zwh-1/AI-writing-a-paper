"""Immediate worker termination that cannot enter a stuck Windows DLL detach."""
import os


if os.name == 'nt':
    import ctypes
    from ctypes import wintypes

    # Resolve these before local model imports can block inside the loader.
    # Loading ctypes/kernel32 lazily in the watchdog could wait on the same lock.
    _kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    _get_current_process = _kernel.GetCurrentProcess
    _get_current_process.argtypes = []
    _get_current_process.restype = wintypes.HANDLE
    _terminate_process = _kernel.TerminateProcess
    _terminate_process.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _terminate_process.restype = wintypes.BOOL
    _current_process = _get_current_process()


def exit_immediately(code):
    """Exit after durable checkpoints without Python/CRT/DLL cleanup.

    Windows CRT _exit enters DLL shutdown, which can deadlock if a model DLL
    is already waiting for a runtime lock. TerminateProcess avoids DLL detach.
    """
    if os.name == 'nt':
        if not _terminate_process(_current_process, code):
            raise ctypes.WinError(ctypes.get_last_error())
        raise RuntimeError('Terminating the current process unexpectedly returned')
    os._exit(code)
