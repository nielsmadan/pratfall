import sys
from collections.abc import Sequence
from pathlib import Path

from pratfall.cli.dispatch import _dispatch, _run_command
from pratfall.cli.parsing import _json_requested, _management_mode, build_parser
from pratfall.cli.presentation import _emit, _silence_broken_stream
from pratfall.errors import PratError


def _main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not _management_mode(arguments):
        return _run_command(arguments, Path.cwd())
    try:
        parser = build_parser()
        args = parser.parse_args(arguments)
        if args.command is None:
            parser.print_help()
            return 0
        return _dispatch(args)
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
