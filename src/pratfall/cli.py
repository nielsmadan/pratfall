import argparse
import json
import math
import os
import shlex
import shutil
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import NoReturn

from pratfall import __version__
from pratfall.adapters.registry import ADAPTERS
from pratfall.catalog import AGENTS, MANAGEMENT_COMMANDS
from pratfall.config import config_path, init_config, load_config, resolve_profile
from pratfall.errors import PratError
from pratfall.models import Config, DecodedOutput, Invocation, Options, ResolvedProfile, ResultError
from pratfall.output import normalize, result_dict, validation_error
from pratfall.prompt_input import InputInterrupted, PromptSource, acquire_prompt
from pratfall.runner import ProcessResult, run

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


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise PratError(message, code="invalid_arguments")


def _common_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config",
        metavar="PATH",
        default=argparse.SUPPRESS,
        help="Use this config path (relative to invocation cwd).",
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

run options:
  --prompt=TEXT           Pass prompt text, including text beginning with a dash.
  -f, --file PATH         Read the prompt from a UTF-8 file; use - for stdin.
  --model MODEL           Override the profile model.
  --effort EFFORT         Override the native effort setting.
  --timeout SECONDS       Set the wall-clock deadline.
  --max-budget-usd USD    Set Claude's native API-call budget.
  --max-turns COUNT       Set a supported native turn limit.
  --max-ai-credits COUNT  Set Copilot's soft per-response AI-credit limit.
  --cwd PATH              Set the agent working directory.
  --config PATH           Use this config path.
  --json                  Print one normalized JSON result.
  --dry-run               Resolve and print the invocation without launching it.

examples:
  prat cx "review this change"
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
    config = subparsers.add_parser(
        "config", help="Manage the global TOML config.", allow_abbrev=False
    )
    _common_flags(config)
    config_commands = config.add_subparsers(
        dest="config_command", required=True, parser_class=Parser
    )
    for name, help_text in (
        ("path", "Print the selected config path."),
        ("init", "Create an example config; fail if the path already exists."),
        ("validate", "Validate every configured profile without launching an agent."),
    ):
        child = config_commands.add_parser(name, help=help_text, allow_abbrev=False)
        _common_flags(child)
    return parser


def _emit(payload: dict[str, object], lines: list[str], *, json_mode: bool) -> None:
    if json_mode:
        print(json.dumps({"schema_version": 1, **payload}, ensure_ascii=False))
    else:
        for line in lines:
            print(line)


def _agents(json_mode: bool) -> None:
    records = []
    lines = []
    for agent in AGENTS:
        caps = agent.capabilities
        capabilities = {
            "model": caps.model,
            "effort": caps.effort,
            "effort_values": list(caps.effort_values) or None,
            "budgets": sorted(caps.budgets),
        }
        records.append(
            {
                "name": agent.name,
                "label": agent.label,
                "aliases": list(agent.aliases),
                "command": list(agent.command),
                "capabilities": capabilities,
            }
        )
        supported = [field for field in ("model", "effort") if getattr(caps, field)]
        supported.extend(sorted(caps.budgets))
        lines.append(f"{agent.name} ({', '.join(agent.aliases)}): {', '.join(supported) or 'none'}")
    _emit({"agents": records}, lines, json_mode=json_mode)


def _profiles(config: Config, json_mode: bool) -> None:
    records = []
    lines = []
    for name in sorted(config.profiles):
        resolved = resolve_profile(config, name)
        options = asdict(resolved.options)
        records.append({"name": name, "agent": resolved.agent.name, "options": options})
        description = f"{name}: {resolved.agent.name}"
        for key, value in options.items():
            if value is None or value == ():
                continue
            rendered = json.dumps(value, ensure_ascii=False) if key == "native_args" else value
            description += f" {key}={rendered}"
        lines.append(description)
    _emit({"profiles": records}, lines or ["No profiles configured."], json_mode=json_mode)


def _doctor(config: Config, json_mode: bool) -> None:
    records = []
    lines = []
    for agent in AGENTS:
        executable = config.commands.get(agent.name, agent.command)[0]
        found = shutil.which(executable)
        records.append(
            {
                "agent": agent.name,
                "executable": executable,
                "path": found,
                "available": found is not None,
            }
        )
        lines.append(
            f"{agent.name}: {found if found is not None else 'unavailable (' + executable + ')'}"
        )
    _emit({"agents": records}, lines, json_mode=json_mode)


def _validate_config_native_arguments(config: Config) -> None:
    for name in config.profiles:
        resolved = resolve_profile(config, name)
        adapter = ADAPTERS.get(resolved.agent.name)
        if adapter is None:
            continue
        try:
            if adapter.validate_resolved is not None:
                adapter.validate_resolved(resolved)
            else:
                adapter.validate(resolved.options.native_args or ())
        except PratError as error:
            raise PratError(f"{config.path}: profiles.{name}.native_args: {error}") from error


def _dispatch(args: argparse.Namespace) -> None:
    if args.command == "config" and args.config_command == "path":
        path = config_path(args.config)
        _emit({"path": str(path)}, [str(path)], json_mode=args.json)
    elif args.command == "config" and args.config_command == "init":
        path = init_config(args.config)
        _emit({"path": str(path), "created": True}, [f"Created {path}"], json_mode=args.json)
    else:
        config = load_config(args.config)
        _validate_config_native_arguments(config)
        if args.command == "agents":
            _agents(args.json)
        elif args.command == "profiles":
            _profiles(config, args.json)
        elif args.command == "doctor":
            _doctor(config, args.json)
        else:
            _emit(
                {"path": str(config.path), "exists": config.exists, "valid": True},
                [
                    f"Valid config: {config.path}"
                    if config.exists
                    else f"No config at {config.path}; using built-in defaults."
                ],
                json_mode=args.json,
            )


def _parse_run(arguments: list[str]) -> RunArguments:
    prat_arguments, native_arguments = _split_native(arguments)
    values: dict[str, str] = {}
    selector: str | None = None
    prompt_source: PromptSource | None = None
    json_mode = False
    dry_run = False
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


def _build_invocation(resolved: ResolvedProfile, prompt: bytes) -> Invocation:
    adapter = ADAPTERS.get(resolved.agent.name)
    if adapter is None:
        raise PratError(
            f"{resolved.agent.label} execution is not implemented yet.",
            code="unsupported_agent",
        )
    return adapter.build(resolved, prompt)


def _decode(resolved: ResolvedProfile, stdout: str) -> DecodedOutput:
    return ADAPTERS[resolved.agent.name].decode(stdout)


def _decode_process(
    resolved: ResolvedProfile, process: ProcessResult
) -> tuple[ProcessResult, DecodedOutput, str]:
    stdout: str | None
    stderr = ""
    encoding_error: ResultError | None = None
    try:
        stdout = process.stdout.decode("utf-8")
    except UnicodeDecodeError:
        stdout = None
        encoding_error = ResultError("output_encoding", "Agent stdout is not valid UTF-8.")
    try:
        stderr = process.stderr.decode("utf-8")
    except UnicodeDecodeError:
        encoding_error = encoding_error or ResultError(
            "output_encoding", "Agent stderr is not valid UTF-8."
        )
    if encoding_error is not None and process.error is None:
        process = replace(process, error=encoding_error)
    decoded = _decode(resolved, stdout) if stdout is not None else DecodedOutput()
    return process, decoded, stderr


def _preview(
    resolved: ResolvedProfile,
    invocation: Invocation,
    cwd: Path,
    *,
    json_mode: bool,
) -> None:
    payload = {
        "schema_version": 1,
        "dry_run": True,
        "agent": resolved.agent.name,
        "profile": resolved.profile,
        "model": resolved.options.model,
        "argv": list(invocation.argv),
        "cwd": str(cwd),
        "timeout": resolved.options.timeout,
        "stdin_bytes": len(invocation.stdin),
    }
    if json_mode:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(f"command: {shlex.join(invocation.argv)}")
        print(f"cwd: {cwd}")
        print(f"timeout: {resolved.options.timeout:g}s")
        print(f"stdin: {len(invocation.stdin)} bytes")


def _emit_result(result: dict[str, object], *, json_mode: bool) -> None:
    if json_mode:
        print(json.dumps(result, ensure_ascii=False))
        return
    output = result["output"]
    if isinstance(output, str) and output:
        sys.stdout.write(output)
        if not output.endswith("\n"):
            sys.stdout.write("\n")
    error = result["error"]
    if isinstance(error, dict):
        print(f"prat: {error['message']}", file=sys.stderr)


def _run_command(arguments: list[str], invocation_cwd: Path) -> int:
    json_mode = _json_requested(arguments)
    resolved: ResolvedProfile | None = None
    try:
        parsed = _parse_run(arguments)
        json_mode = parsed.json
        config = load_config(parsed.config, cwd=invocation_cwd)
        _validate_config_native_arguments(config)
        resolved = resolve_profile(config, parsed.selector, parsed.options)
        prompt = acquire_prompt(parsed.prompt_source, invocation_cwd)
        cwd = _run_cwd(parsed.cwd, invocation_cwd)
        invocation = _build_invocation(resolved, prompt)
        if parsed.dry_run:
            _preview(resolved, invocation, cwd, json_mode=json_mode)
            return 0
        print(f"prat: launching {resolved.agent.label}", file=sys.stderr)
        timeout = resolved.options.timeout
        if timeout is None:
            raise PratError("Resolved timeout is missing.", code="invalid_arguments")
        process = run(invocation, cwd, timeout)
        process, decoded, native_stderr = _decode_process(resolved, process)
        if native_stderr:
            sys.stderr.write(native_stderr)
            if not native_stderr.endswith("\n"):
                sys.stderr.write("\n")
        result = normalize(resolved, process, decoded)
        print(
            f"prat: {resolved.agent.label} finished with status {result.status} "
            f"in {result.duration_ms}ms",
            file=sys.stderr,
        )
        payload = result_dict(result)
        _emit_result(payload, json_mode=json_mode)
        return result.exit_code
    except InputInterrupted as error:
        exit_code = 128 + error.signum
        payload = validation_error(error, "interrupted", exit_code)
        payload["status"] = "interrupted"
        if resolved is not None:
            payload.update(
                agent=resolved.agent.name,
                profile=resolved.profile,
                model=resolved.options.model,
            )
        if json_mode:
            print(json.dumps(payload, ensure_ascii=False))
        else:
            print(f"prat: {error}", file=sys.stderr)
        return exit_code
    except PratError as error:
        payload = validation_error(error, error.code, error.exit_code)
        if resolved is not None:
            payload.update(
                agent=resolved.agent.name,
                profile=resolved.profile,
                model=resolved.options.model,
            )
        if json_mode:
            print(json.dumps(payload, ensure_ascii=False))
        else:
            print(f"prat: {error}", file=sys.stderr)
        return error.exit_code


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
        if argument in {"--json", "--dry-run"} or argument.startswith("--prompt="):
            run_option = run_option or argument != "--json"
            index += 1
            continue
        if argument.startswith("-"):
            index += 1
            continue
        return argument in MANAGEMENT_COMMANDS
    return not run_option


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
        _dispatch(args)
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
    return 0


def _silence_broken_stdout() -> None:
    descriptor = os.open(os.devnull, os.O_WRONLY)
    try:
        try:
            os.dup2(descriptor, sys.stdout.fileno())
        except (AttributeError, OSError, ValueError):
            sys.stdout = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
    finally:
        os.close(descriptor)


def main(argv: Sequence[str] | None = None) -> int:
    try:
        try:
            exit_code = _main(argv)
        except SystemExit:
            sys.stdout.flush()
            raise
        sys.stdout.flush()
    except BrokenPipeError:
        _silence_broken_stdout()
        return 1
    return exit_code
