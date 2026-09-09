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
from pratfall.runner import ProcessResult, run

PROMPT_LIMIT = 1024 * 1024
_RUN_VALUE_FLAGS = {
    "--config": "config",
    "--cwd": "cwd",
    "--effort": "effort",
    "--max-ai-credits": "max_ai_credits",
    "--max-budget-usd": "max_budget_usd",
    "--max-turns": "max_turns",
    "--model": "model",
    "--timeout": "timeout",
}


@dataclass(frozen=True)
class RunArguments:
    selector: str
    prompt: str
    prompt_from_stdin: bool
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
        epilog="Run: prat [OPTIONS] SELECTOR PROMPT [-- NATIVE_ARGS]",
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
    prompt: str | None = None
    prompt_option = False
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
            if prompt is not None:
                raise PratError("Provide exactly one prompt.", code="invalid_arguments")
            prompt = argument.removeprefix("--prompt=")
            prompt_option = True
            index += 1
            continue
        name, equals, inline = argument.partition("=")
        field = _RUN_VALUE_FLAGS.get(name)
        if field is not None:
            value, index = _run_option_value(prat_arguments, index, name, equals, inline)
            values[field] = value
            continue
        if argument.startswith("-") and argument != "-":
            raise PratError(f"unrecognized argument: {argument}", code="invalid_arguments")
        selector, prompt = _add_positional(selector, prompt, argument)
        index += 1
    if selector is None:
        raise PratError("A selector is required.", code="invalid_arguments")
    if prompt is None:
        raise PratError("Provide exactly one prompt.", code="invalid_arguments")
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
        prompt,
        not prompt_option and prompt == "-",
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


def _add_positional(
    selector: str | None, prompt: str | None, argument: str
) -> tuple[str | None, str | None]:
    if selector is None:
        return argument, prompt
    if prompt is None:
        return selector, argument
    raise PratError("Provide exactly one prompt.", code="invalid_arguments")


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


def _prompt_bytes(argument: str, from_stdin: bool) -> bytes:
    if from_stdin:
        stream = getattr(sys.stdin, "buffer", sys.stdin)
        value = stream.read(PROMPT_LIMIT + 1)
        prompt = value.encode("utf-8") if isinstance(value, str) else value
    else:
        try:
            prompt = argument.encode("utf-8")
        except UnicodeEncodeError as error:
            raise PratError("Prompt is not valid UTF-8.", code="invalid_arguments") from error
    if not prompt:
        raise PratError("Prompt must not be empty.", code="invalid_arguments")
    if b"\0" in prompt:
        raise PratError("Prompt must not contain NUL bytes.", code="invalid_arguments")
    if len(prompt) > PROMPT_LIMIT:
        raise PratError(f"Prompt exceeds the {PROMPT_LIMIT} byte limit.", code="invalid_arguments")
    if from_stdin:
        try:
            prompt.decode("utf-8")
        except UnicodeDecodeError as error:
            raise PratError(
                "Standard input prompt is not valid UTF-8.", code="invalid_arguments"
            ) from error
    return prompt


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
        prompt = _prompt_bytes(parsed.prompt, parsed.prompt_from_stdin)
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


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not _management_mode(arguments):
        try:
            return _run_command(arguments, Path.cwd())
        except BrokenPipeError:
            return 1
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
    except BrokenPipeError:
        return 1
    return 0
