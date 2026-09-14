import argparse
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

from pratfall import __version__
from pratfall.catalog import MANAGEMENT_COMMANDS
from pratfall.errors import PratError
from pratfall.models import Options
from pratfall.prompt_input import PromptSource

_RUN_VALUE_FLAGS = {
    "--config": "config",
    "--cwd": "cwd",
    "--effort": "effort",
    "--max-ai-credits": "max_ai_credits",
    "--max-budget-usd": "max_budget_usd",
    "--max-turns": "max_turns",
    "--model": "model",
    "--timeout": "timeout",
    "--file": "file",
    "-f": "file",
}


@dataclass(frozen=True)
class RunArguments:
    selector: str
    prompt_source: PromptSource | None
    config: str | None
    cwd: str | None
    json: bool
    dry_run: bool
    options: Options
    progress: bool


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise PratError(message, code="invalid_arguments")


def _common_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config",
        metavar="PATH",
        default=argparse.SUPPRESS,
        help="Merge PATH over global config instead of .pratfile (relative to invocation cwd).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Print a JSON management result.",
    )


def build_parser() -> Parser:
    parser = Parser(
        prog="prat",
        description="Run installed coding agents through named profiles.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""run syntax:
  prat [RUN_OPTIONS] SELECTOR PROMPT [-- NATIVE_ARGS]

SELECTOR is an agent name (codex, kiro), alias (cx), or profile. See prat agents.

run options:
  --prompt=TEXT           Pass prompt text, including text beginning with a dash.
  -f, --file PATH         Read the prompt from a UTF-8 file; use - for stdin.
  --model MODEL           Override the profile model.
  --effort EFFORT         Override the native effort setting.
  --fast / --no-fast      Enable or disable supported native fast mode for this run.
  --timeout SECONDS       Set the wall-clock deadline.
  --max-budget-usd USD    Set a supported native USD budget.
  --max-turns COUNT       Set a supported native turn limit.
  --max-ai-credits COUNT  Set Copilot's soft per-response AI-credit limit.
  --cwd PATH              Set the agent working directory.
  --config PATH           Merge PATH over global config instead of .pratfile.
  --json                  Print one normalized JSON result.
  --progress              Print bounded live activity updates on stderr.
  --dry-run               Resolve and print the invocation without launching it.

examples:
  prat codex "review this change"
  prat simple "review this change" --effort low
  printf 'multiline prompt\\n' | prat cc -
  prat cc --file prompt.md
  prat cc --prompt=-leading-dash
""",
        allow_abbrev=False,
    )
    parser.set_defaults(config=None, json=False)
    _common_flags(parser)
    parser.add_argument("--version", action="version", version=f"prat {__version__}")
    subparsers = parser.add_subparsers(dest="command", parser_class=Parser)
    for command, help_text in (
        ("agents", "List built-in agent selectors and supported settings."),
        ("profiles", "List configured profiles and their resolved settings."),
        ("doctor", "Locate agent executables without running them or checking credentials."),
    ):
        child = subparsers.add_parser(command, help=help_text, allow_abbrev=False)
        _common_flags(child)
        if command == "doctor":
            child.add_argument(
                "--versions",
                action="store_true",
                help=(
                    "Execute each available configured command prefix with its native version "
                    "arguments (version for Amp, --version for other agents); "
                    "configured wrappers may have side effects."
                ),
            )
    config = subparsers.add_parser("config", help="Manage TOML configuration.", allow_abbrev=False)
    _common_flags(config)
    config_commands = config.add_subparsers(
        dest="config_command", required=True, parser_class=Parser
    )
    for name, help_text in (
        ("path", "Print the global config path, or --config PATH."),
        ("init", "Create an example config; fail if the path already exists."),
        ("validate", "Validate every configured profile without launching an agent."),
    ):
        child = config_commands.add_parser(name, help=help_text, allow_abbrev=False)
        _common_flags(child)
    return parser


def _parse_run(arguments: list[str]) -> RunArguments:
    prat_arguments, native_arguments = _split_native(arguments)
    values: dict[str, str] = {}
    selector: str | None = None
    prompt_source: PromptSource | None = None
    json_mode = False
    dry_run = False
    progress = False
    fast: bool | None = None
    index = 0
    while index < len(prat_arguments):
        argument = prat_arguments[index]
        if argument == "--json":
            json_mode = True
            index += 1
            continue
        if argument == "--dry-run":
            dry_run = True
            index += 1
            continue
        if argument == "--progress":
            progress = True
            index += 1
            continue
        if argument in {"--fast", "--no-fast"}:
            fast_value = argument == "--fast"
            if fast is not None and fast != fast_value:
                raise PratError(
                    "--fast and --no-fast cannot be used together.",
                    code="invalid_arguments",
                )
            fast = fast_value
            index += 1
            continue
        if argument == "--prompt":
            raise PratError("--prompt requires the --prompt=TEXT form.", code="invalid_arguments")
        if argument.startswith("--prompt="):
            prompt_source = _add_prompt_source(
                prompt_source, PromptSource("inline", argument.removeprefix("--prompt="))
            )
            index += 1
            continue
        name, equals, inline = argument.partition("=")
        field = _RUN_VALUE_FLAGS.get(name)
        if field is not None:
            value, index = _run_option_value(prat_arguments, index, name, equals, inline)
            if field == "file":
                prompt_source = _add_prompt_source(
                    prompt_source, PromptSource("file", _text_option(value, name))
                )
            else:
                values[field] = value
            continue
        if argument.startswith("-") and argument != "-":
            raise PratError(f"unrecognized argument: {argument}", code="invalid_arguments")
        if selector is None:
            selector = argument
        else:
            source = PromptSource("stdin") if argument == "-" else PromptSource("inline", argument)
            prompt_source = _add_prompt_source(prompt_source, source)
        index += 1
    if selector is None:
        raise PratError("A selector is required.", code="invalid_arguments")
    options = Options(
        model=_text_option(values.get("model"), "--model"),
        effort=_text_option(values.get("effort"), "--effort"),
        timeout=_number_option(values.get("timeout"), "--timeout"),
        max_budget_usd=_number_option(values.get("max_budget_usd"), "--max-budget-usd"),
        max_turns=_integer_option(values.get("max_turns"), "--max-turns"),
        max_ai_credits=_number_option(values.get("max_ai_credits"), "--max-ai-credits"),
        fast=fast,
        native_args=native_arguments,
    )
    return RunArguments(
        selector,
        prompt_source,
        _text_option(values.get("config"), "--config"),
        _text_option(values.get("cwd"), "--cwd"),
        json_mode,
        dry_run,
        options,
        progress,
    )


def _split_native(arguments: list[str]) -> tuple[list[str], tuple[str, ...] | None]:
    if "--" not in arguments:
        return arguments, None
    delimiter = arguments.index("--")
    return arguments[:delimiter], tuple(arguments[delimiter + 1 :])


def _run_option_value(
    arguments: list[str], index: int, name: str, equals: str, inline: str
) -> tuple[str, int]:
    if equals:
        return inline, index + 1
    if index + 1 >= len(arguments):
        raise PratError(f"{name} requires a value.", code="invalid_arguments")
    return arguments[index + 1], index + 2


def _add_prompt_source(current: PromptSource | None, added: PromptSource) -> PromptSource:
    if current is not None:
        raise PratError("Provide exactly one prompt source.", code="invalid_arguments")
    return added


def _text_option(value: str | None, flag: str) -> str | None:
    if value is not None and (not value.strip() or "\0" in value):
        raise PratError(f"{flag} requires a nonempty value.", code="invalid_arguments")
    return value


def _number_option(value: str | None, flag: str) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except ValueError:
        number = math.nan
    if not math.isfinite(number) or number <= 0:
        raise PratError(f"{flag} requires a finite positive number.", code="invalid_arguments")
    return number


def _integer_option(value: str | None, flag: str) -> int | None:
    if value is None:
        return None
    try:
        number = int(value)
    except ValueError:
        number = 0
    if str(number) != value or number <= 0:
        raise PratError(f"{flag} requires a positive integer.", code="invalid_arguments")
    return number


def _run_cwd(value: str | None, invocation_cwd: Path) -> Path:
    if value is None:
        return invocation_cwd
    try:
        path = Path(os.path.abspath(invocation_cwd / Path(value).expanduser()))
    except RuntimeError as error:
        raise PratError(
            f"--cwd cannot expand {value!r}; use an absolute path or a valid ~user path.",
            code="invalid_arguments",
        ) from error
    if not path.is_dir():
        raise PratError(f"--cwd is not a directory: {path}", code="invalid_arguments")
    return path


def _json_requested(arguments: list[str]) -> bool:
    before_delimiter = arguments[: arguments.index("--")] if "--" in arguments else arguments
    index = 0
    while index < len(before_delimiter):
        argument = before_delimiter[index]
        name = argument.partition("=")[0]
        if name in _RUN_VALUE_FLAGS:
            index += 1 if "=" in argument else 2
        elif argument == "--json":
            return True
        else:
            index += 1
    return False


def _management_mode(arguments: list[str]) -> bool:
    run_option = False
    index = 0
    while index < len(arguments) and arguments[index] != "--":
        argument = arguments[index]
        name = argument.partition("=")[0]
        if name in _RUN_VALUE_FLAGS:
            run_option = run_option or name != "--config"
            index += 1 if "=" in argument else 2
            continue
        if argument in {
            "--json",
            "--dry-run",
            "--progress",
            "--fast",
            "--no-fast",
        } or argument.startswith("--prompt="):
            run_option = run_option or argument != "--json"
            index += 1
            continue
        if argument.startswith("-"):
            index += 1
            continue
        return argument in MANAGEMENT_COMMANDS
    return not run_option
