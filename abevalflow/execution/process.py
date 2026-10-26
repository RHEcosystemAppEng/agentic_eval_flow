"""Bound external commands and reap their entire process group before returning."""

from __future__ import annotations

import os
import signal
import subprocess


def run_process(command, *, timeout, cwd=None, env=None, stdout=None, stderr=None, grace=30) -> int:
    """Allow native signal cleanup, then kill remaining descendants on timeout/interruption."""
    with subprocess.Popen(command, cwd=cwd, env=env, stdout=stdout, stderr=stderr, start_new_session=True) as process:
        try:
            return process.wait(timeout=timeout)
        except BaseException:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=grace)
            except subprocess.TimeoutExpired:
                pass
            finally:
                # The parent may have exited while a grandchild still runs.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
            raise
