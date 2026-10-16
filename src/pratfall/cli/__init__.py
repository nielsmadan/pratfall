import os
import signal
import sys
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import BinaryIO

from pratfall.cli.dispatch import _dispatch, _run_command, _step_command, _step_failure
from pratfall.cli.parsing import _json_requested, _management_mode, build_parser
from pratfall.cli.presentation import _emit, _silence_broken_stream
from pratfall.cli.shell_completion import _complete_command
from pratfall.errors import PratError
from pratfall.limits import WORKFLOW_WATCHDOG_INTERVAL


def _watch_parent(
    original: int,
    interval: float,
    terminate: Callable[[], None],
    *,
    getppid: Callable[[], int] = os.getppid,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    while getppid() == original:
        sleep(interval)
    terminate()


def _start_parent_watchdog(parent: int) -> None:
    watcher = threading.Thread(
        target=_watch_parent,
        args=(parent, WORKFLOW_WATCHDOG_INTERVAL, lambda: os.kill(os.getpid(), signal.SIGTERM)),
        daemon=True,
    )
    watcher.start()


def _detach_stdin() -> BinaryIO:
    spec = os.fdopen(os.dup(0), "rb")
    null = os.open(os.devnull, os.O_RDONLY)
    try:
        os.dup2(null, 0)
    finally:
        os.close(null)
    return spec


def _step_entry() -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    try:
        spec = _detach_stdin()
    except OSError as error:
        return _step_failure(
            PratError(f"Cannot set up workflow step input: {error}.", code="invalid_arguments")
        )
    with spec:
        return _step_command(spec, Path.cwd(), os.environ, _start_parent_watchdog)


def _main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments[:1] == ["__complete"]:
        return _complete_command(arguments[1:], os.environ)
    if arguments[:1] == ["__step"]:
        return _step_entry()
    if not _management_mode(arguments):
        return _run_command(arguments, Path.cwd(), os.environ)
    try:
        parser = build_parser()
        args = parser.parse_args(arguments)
        if args.command is None:
            parser.print_help()
            return 0
        return _dispatch(args, Path.cwd(), os.environ)
    except PratError as error:
        if _json_requested(arguments):
            _emit(
                {
                    "status": "error",
                    "exit_code": error.exit_code,
                    "error": {"code": error.code, "message": str(error)},
                },
                [],
                json_mode=True,
            )
        else:
            print(f"prat: {error}", file=sys.stderr)
        return error.exit_code


def main(argv: Sequence[str] | None = None) -> int:
    try:
        try:
            exit_code = _main(argv)
        except SystemExit:
            sys.stdout.flush()
            raise
        sys.stdout.flush()
    except BrokenPipeError:
        _silence_broken_stream("stdout")
        return 1
    return exit_code
