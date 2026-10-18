"""Run one render inside an owned OS container; wait for its workers before returning."""
from __future__ import annotations

import os
import signal
import subprocess
import time

import psutil


class _WindowsJob:
    def __init__(self):
        import ctypes as c
        from ctypes import wintypes as w

        class Limits(c.Structure):
            _fields_ = [("process_time", c.c_longlong), ("job_time", c.c_longlong),
                        ("flags", w.DWORD), ("minimum", c.c_size_t), ("maximum", c.c_size_t),
                        ("active", w.DWORD), ("affinity", c.c_size_t),
                        ("priority", w.DWORD), ("scheduling", w.DWORD)]

        class Counters(c.Structure):
            _fields_ = [(name, c.c_ulonglong) for name in
                        ("reads", "writes", "other", "read_bytes", "write_bytes", "other_bytes")]

        class Extended(c.Structure):
            _fields_ = [("limits", Limits), ("io", Counters)] + [
                (name, c.c_size_t) for name in ("process_memory", "job_memory", "peak_process", "peak_job")]

        class Accounting(c.Structure):
            _fields_ = [(name, c.c_longlong) for name in ("user", "kernel", "period_user", "period_kernel")] + [
                (name, w.DWORD) for name in ("faults", "total", "active", "terminated")]

        self.c, self.Accounting = c, Accounting
        self.api = c.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "CreateJobObjectW": ([c.c_void_p, w.LPCWSTR], w.HANDLE),
            "SetInformationJobObject": ([w.HANDLE, c.c_int, c.c_void_p, w.DWORD], w.BOOL),
            "QueryInformationJobObject": ([w.HANDLE, c.c_int, c.c_void_p, w.DWORD, c.c_void_p], w.BOOL),
            "AssignProcessToJobObject": ([w.HANDLE, w.HANDLE], w.BOOL),
            "TerminateJobObject": ([w.HANDLE, w.UINT], w.BOOL),
            "OpenThread": ([w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
            "GetProcessIdOfThread": ([w.HANDLE], w.DWORD),
            "ResumeThread": ([w.HANDLE], w.DWORD),
            "CloseHandle": ([w.HANDLE], w.BOOL),
        }
        for name, (args, result) in signatures.items():
            fn = getattr(self.api, name)
            fn.argtypes, fn.restype = args, result
        self.handle = self.api.CreateJobObjectW(None, None)
        self._check(self.handle)
        limits = Extended()
        limits.limits.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE, with no breakaway permission
        try:
            self._check(self.api.SetInformationJobObject(self.handle, 9, c.byref(limits), c.sizeof(limits)))
        except BaseException:
            self.close()
            raise

    def _check(self, result):
        if not result:
            raise self.c.WinError(self.c.get_last_error())
        return result

    def assign(self, proc):
        # Popen owns this exact kernel handle; reopening by PID could assign a reused PID.
        self._check(self.api.AssignProcessToJobObject(self.handle, int(proc._handle)))
        threads = psutil.Process(proc.pid).threads()
        if proc.poll() is not None or len(threads) != 1:
            raise RuntimeError("Suspended render has no unique primary thread")
        thread = self._check(self.api.OpenThread(0x0002 | 0x0800, False, threads[0].id))
        try:
            if self.api.GetProcessIdOfThread(thread) != proc.pid or proc.poll() is not None:
                raise RuntimeError("Render thread ownership changed")
            previous = self.api.ResumeThread(thread)
            if previous == 0xFFFFFFFF:
                self._check(0)
            if previous != 1:
                raise RuntimeError("Render primary thread was not suspended exactly once")
        finally:
            self.api.CloseHandle(thread)

    def stop(self):
        self._check(self.api.TerminateJobObject(self.handle, 1))
        deadline = time.monotonic() + 10
        while True:
            info = self.Accounting()
            self._check(self.api.QueryInformationJobObject(
                self.handle, 1, self.c.byref(info), self.c.sizeof(info), None))
            if not info.active:
                return
            if time.monotonic() >= deadline:
                raise RuntimeError("Owned render job still has active workers")
            time.sleep(0.02)

    def close(self):
        if self.handle:
            handle, self.handle = self.handle, None
            self._check(self.api.CloseHandle(handle))


def _stop_group(pgid: int):
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + 10
    while True:
        active = False
        for proc in psutil.process_iter(["pid", "status"]):
            try:
                if os.getpgid(proc.pid) == pgid and proc.status() != psutil.STATUS_ZOMBIE:
                    active = True
                    break
            except (ProcessLookupError, psutil.NoSuchProcess):
                continue
        if not active:
            return
        if time.monotonic() >= deadline:
            raise RuntimeError("Owned render process group still has active workers")
        time.sleep(0.02)


def run(args, *, timeout=None, capture_output=False, input=None, check=False, **kwargs):
    """subprocess.run's result/error contract, with containment for this render's descendants."""
    if kwargs.get("shell"):
        raise ValueError("Render commands must be an argument list, without shell=True")
    if capture_output:
        if "stdout" in kwargs or "stderr" in kwargs:
            raise ValueError("stdout/stderr cannot be used with capture_output")
        kwargs.update(stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if input is not None:
        if kwargs.get("stdin") is not None:
            raise ValueError("stdin cannot be used with input")
        kwargs["stdin"] = subprocess.PIPE
    job = None
    if os.name == "nt":
        job = _WindowsJob()
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | 0x00000004  # CREATE_SUSPENDED
    else:
        if kwargs.get("start_new_session") is False or kwargs.get("process_group") not in (None, -1):
            raise ValueError("Render requires its own process session")
        kwargs["start_new_session"] = True
    proc = None
    stopped = False
    try:
        proc = subprocess.Popen(args, **kwargs)
        if job:
            job.assign(proc)
        try:
            stdout, stderr = proc.communicate(input, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            job.stop() if job else _stop_group(proc.pid)
            stopped = True
            stdout, stderr = proc.communicate(timeout=10)
            exc.output, exc.stderr = stdout, stderr
            raise
        result = subprocess.CompletedProcess(args, proc.returncode, stdout, stderr)
        if check:
            result.check_returncode()
        return result
    finally:
        try:
            if proc is not None:
                if not stopped:
                    job.stop() if job else _stop_group(proc.pid)
                # Setup can fail before the suspended process has joined its Job.
                if proc.poll() is None:
                    proc.kill()
                proc.wait(timeout=10)
                for stream in (proc.stdin, proc.stdout, proc.stderr):
                    if stream:
                        stream.close()
        finally:
            if job:
                job.close()
