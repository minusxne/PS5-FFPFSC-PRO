"""Process entry point.

The same executable runs in two roles: the GUI, and (with ``--worker``) the
job worker subprocess the GUI spawns for compress/unpack/verify jobs. Running
jobs out of process keeps the UI responsive, lets Cancel kill mkpfs's whole
process tree, and means an out-of-memory crash never takes the window down.
"""

from __future__ import annotations

import multiprocessing
import sys


def main(argv: list[str] | None = None) -> int:
    # Must run first: in a frozen build, mkpfs's multiprocessing children
    # re-launch this executable and are dispatched from here.
    multiprocessing.freeze_support()

    args = sys.argv[1:] if argv is None else argv
    if args[:1] == ["--worker"]:
        from ps5ffpfsc.core.worker import worker_main

        return worker_main()

    from ps5ffpfsc.ui.app import run_gui

    return run_gui(args)


if __name__ == "__main__":
    raise SystemExit(main())
