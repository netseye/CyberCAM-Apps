"""Exec a wake-word process that terminates when its Python parent dies."""

import ctypes
import os
import signal
import sys


PR_SET_PDEATHSIG = 1


def arm_parent_death_signal(expected_parent):
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(PR_SET_PDEATHSIG, signal.SIGTERM, 0, 0, 0) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    # The parent may have died between fork/exec and prctl().
    if os.getppid() != expected_parent:
        os.kill(os.getpid(), signal.SIGTERM)


def main(argv):
    if len(argv) < 3:
        raise SystemExit("usage: parent_guard.py PARENT_PID COMMAND [ARG ...]")
    expected_parent = int(argv[1])
    command = argv[2:]
    arm_parent_death_signal(expected_parent)
    os.execvpe(command[0], command, os.environ)


if __name__ == "__main__":
    main(sys.argv)
