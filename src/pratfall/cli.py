import argparse
import errno
import fcntl
import json
import math
import os
import shlex
import shutil
import sys
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import closing, contextmanager, suppress
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import ClassVar, Literal, NoReturn, Protocol

from pratfall import __version__
from pratfall.adapters.registry import ADAPTERS
from pratfall.catalog import AGENTS, MANAGEMENT_COMMANDS
from pratfall.codes import SIGNAL_EXIT_BASE
from pratfall.config import config_path, init_config, load_config, option_labels, resolve_profile
from pratfall.consumer import ByteConsumer
from pratfall.errors import PratError
from pratfall.interruption import InterruptionState, handler_for, handling
from pratfall.models import (
    Activity,
    Config,
    ConsumedCapture,
    DecodedOutput,
    Invocation,
    Options,
    RawCapture,
    ResolvedProfile,
    ResultError,
)
from pratfall.output import normalize, result_dict, validation_error
from pratfall.prompt_input import InputInterrupted, PromptSource, acquire_prompt
from pratfall.runner import (
    OutputLimits,
    ProcessResult,
    cleanup_process_group,
    raw_stdout,
    run,
)

VERSION_TIMEOUT = 3.0
VERSION_OUTPUT_LIMIT = 64 * 1024
VERSION_LIMITS = OutputLimits(stdout=VERSION_OUTPUT_LIMIT, stderr=VERSION_OUTPUT_LIMIT)
ACTIVITY_LABELS: Mapping[Activity, str] = MappingProxyType(
    {
        "starting": "starting",
        "working": "working",
        "reasoning": "reasoning",
        "tool": "using tools",
        "answering": "answering",
        "finishing": "finishing",
    }
)

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


@dataclass
class _DoctorState(InterruptionState):
    result_chosen: bool = False


class _DoctorInterrupted(Exception):
    pass


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
            "fast": caps.fast,
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
        supported = [field for field in ("model", "effort", "fast") if getattr(caps, field)]
        supported.extend(sorted(caps.budgets))
        selectors = agent.name
        if agent.aliases:
            selectors += f" ({', '.join(agent.aliases)})"
        lines.append(f"{selectors}: {', '.join(supported) or 'none'}")
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


def _doctor_inventory(
    config: Config, interruption: InterruptionState | None = None
) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for agent in AGENTS:
        if interruption is not None and interruption.received is not None:
            break
        command = config.commands.get(agent.name, agent.command)
        executable = command[0]
        found = shutil.which(executable)
        records.append(
            {
                "agent": agent.name,
                "executable": executable,
                "path": found,
                "available": found is not None,
                "version": None,
                "version_error": None,
            }
        )
    return records


@contextmanager
def _track_interruption(state: _DoctorState) -> Iterator[None]:
    raise_immediately = True

    def on_first(_signum: int) -> None:
        if raise_immediately and not state.result_chosen:
            raise _DoctorInterrupted

    with handling(handler_for(state, on_first=on_first)):
        try:
            yield
        finally:
            raise_immediately = False


def _doctor(config: Config, json_mode: bool, *, versions: bool) -> int:
    records: list[dict[str, object]] = []
    if not versions:
        records = _doctor_inventory(config)
        _emit(
            {"agents": records},
            [_doctor_line(record, versions=False) for record in records],
            json_mode=json_mode,
        )
        return 0

    interruption = _DoctorState()
    try:
        with _track_interruption(interruption):
            try:
                records = _doctor_inventory(config, interruption)
                for agent, record in zip(AGENTS, records, strict=True):
                    if interruption.received is not None:
                        break
                    if not record["available"]:
                        continue
                    command = config.commands.get(agent.name, agent.command)
                    process = run(
                        Invocation((*command, *agent.version_args), b""),
                        Path.cwd(),
                        VERSION_TIMEOUT,
                        output_limits=VERSION_LIMITS,
                    )
                    version, version_error = _version_result(process)
                    record["version"] = version
                    record["version_error"] = version_error
                    if process.interrupted_by is not None:
                        interruption.received = process.interrupted_by
                prepared = _doctor_result(records, versions=True, interrupted=interruption.received)
                interruption.result_chosen = True
            except _DoctorInterrupted:
                prepared = _doctor_result(records, versions=True, interrupted=interruption.received)
                interruption.result_chosen = True
    except _DoctorInterrupted:
        prepared = _doctor_result(records, versions=True, interrupted=interruption.received)
        interruption.result_chosen = True

    payload, lines, exit_code, message = prepared
    _emit(payload, lines, json_mode=json_mode)
    if message is not None and not json_mode:
        print(f"prat: {message}", file=sys.stderr)
    return exit_code


def _doctor_result(
    records: list[dict[str, object]], *, versions: bool, interrupted: int | None
) -> tuple[dict[str, object], list[str], int, str | None]:
    lines = [_doctor_line(record, versions=versions) for record in records]
    payload: dict[str, object] = {"agents": records}
    if interrupted is not None:
        message = f"Interrupted by signal {interrupted}."
        payload.update(
            status="interrupted",
            exit_code=SIGNAL_EXIT_BASE + interrupted,
            error={"code": "interrupted", "message": message},
        )
        return payload, lines, SIGNAL_EXIT_BASE + interrupted, message
    return payload, lines, 0, None


def _version_result(process: ProcessResult) -> tuple[str | None, str | None]:
    if process.error is not None:
        return None, process.error.message
    if process.native_exit_code != 0:
        return None, f"Version probe exited with status {process.native_exit_code}."
    try:
        stdout = raw_stdout(process.capture).decode("utf-8").strip()
    except UnicodeDecodeError:
        return None, "Version output is not valid UTF-8."
    if stdout:
        return stdout, None
    try:
        stderr = process.stderr.decode("utf-8").strip()
    except UnicodeDecodeError:
        return None, "Version output is not valid UTF-8."
    if stderr:
        return stderr, None
    return None, "Version probe returned no version text."


def _doctor_line(record: dict[str, object], *, versions: bool) -> str:
    name = record["agent"]
    found = record["path"]
    executable = record["executable"]
    line = f"{name}: {found if found is not None else 'unavailable (' + str(executable) + ')'}"
    if not versions or not record["available"]:
        return line
    if record["version_error"] is not None:
        return f"{line} version_error={record['version_error']}"
    return f"{line} version={json.dumps(record['version'], ensure_ascii=False)}"


class _Diagnostics(Protocol):
    start_errors: ClassVar[tuple[type[Exception], ...]]
    write_errors: ClassVar[tuple[type[Exception], ...]]

    def open(self) -> None: ...

    def close(self) -> None: ...

    def progress_callback(self) -> Callable[[int, Activity], None] | None: ...

    def fail(self) -> None: ...

    def line(self, value: str) -> None: ...

    def text(self, value: str) -> None: ...

    def flush(self) -> None: ...


class _StreamDiagnostics:
    start_errors: ClassVar[tuple[type[Exception], ...]] = (OSError, ValueError)
    write_errors: ClassVar[tuple[type[Exception], ...]] = (OSError, ValueError)

    def __init__(self) -> None:
        self.failed = False

    def open(self) -> None:
        return None

    def close(self) -> None:
        return None

    def progress_callback(self) -> Callable[[int, Activity], None] | None:
        return None

    def fail(self) -> None:
        self.failed = True
        _silence_broken_stream("stderr")

    def line(self, value: str) -> None:
        if self.failed:
            return
        print(value, file=sys.stderr)

    def text(self, value: str) -> None:
        if self.failed:
            return
        sys.stderr.write(value)

    def flush(self) -> None:
        if self.failed:
            return
        sys.stderr.flush()


class _ProgressDiagnostics:
    start_errors: ClassVar[tuple[type[Exception], ...]] = (AttributeError, OSError, ValueError)
    write_errors: ClassVar[tuple[type[Exception], ...]] = (OSError,)

    def __init__(self) -> None:
        self.failed = False
        self.fd: int | None = None
        self.flags = 0

    def open(self) -> None:
        if self.fd is not None:
            return
        descriptor = sys.stderr.fileno()
        flags = fcntl.fcntl(descriptor, fcntl.F_GETFL)
        fcntl.fcntl(descriptor, fcntl.F_SETFL, flags | os.O_NONBLOCK)
        self.fd = descriptor
        self.flags = flags

    def close(self) -> None:
        descriptor, self.fd = self.fd, None
        if descriptor is None:
            return
        with suppress(OSError):
            fcntl.fcntl(descriptor, fcntl.F_SETFL, self.flags)

    def progress_callback(self) -> Callable[[int, Activity], None] | None:
        return self.progress

    def fail(self) -> None:
        self.failed = True

    def line(self, value: str) -> None:
        self.text(value + "\n")

    def text(self, value: str) -> None:
        descriptor = self.fd
        if self.failed or descriptor is None:
            return
        data = value.encode("utf-8", errors="replace")
        offset = 0
        while offset < len(data):
            try:
                written = os.write(descriptor, data[offset : offset + 4096])
            except BlockingIOError:
                return
            except OSError as error:
                if error.errno in {errno.EAGAIN, errno.EWOULDBLOCK}:
                    return
                self.failed = True
                raise
            if written == 0:
                self.failed = True
                raise OSError(errno.EIO, "stderr write returned zero bytes")
            offset += written

    def flush(self) -> None:
        return None

    def progress(self, elapsed_ms: int, category: Activity) -> None:
        self.line(f"prat: {elapsed_ms / 1000:.1f}s {ACTIVITY_LABELS[category]}")


def _diagnostics_for(*, progress: bool) -> _Diagnostics:
    return _ProgressDiagnostics() if progress else _StreamDiagnostics()


def _validate_native_arguments(resolved: ResolvedProfile, label: str) -> None:
    adapter = ADAPTERS.get(resolved.agent.name)
    if adapter is None:
        return
    try:
        adapter.validate(resolved)
    except PratError as error:
        raise PratError(f"{label}: {error}", code=error.code) from error


def _validate_config_native_arguments(config: Config) -> None:
    for name in config.profiles:
        resolved = resolve_profile(config, name)
        source = config.profiles[name].source or config.path
        label = option_labels(config, name).get(
            "native_args", f"{source}: profiles.{name}.native_args"
        )
        try:
            _validate_native_arguments(resolved, label)
        except PratError as error:
            raise PratError(str(error)) from error


def _config_warnings(config: Config, diagnostics: _Diagnostics) -> None:
    if not config.warnings:
        return
    try:
        diagnostics.open()
        for warning in config.warnings:
            diagnostics.line(f"prat: warning: {warning}")
        diagnostics.flush()
    except (AttributeError, OSError, ValueError) as error:
        diagnostics.close()
        _silence_broken_stream("stderr")
        raise PratError(
            f"Cannot write config warnings: {error}.", code="output_io_error"
        ) from error


def _dispatch(args: argparse.Namespace) -> int:
    if args.command == "config" and args.config_command == "path":
        path = config_path(args.config)
        _emit({"path": str(path)}, [str(path)], json_mode=args.json)
    elif args.command == "config" and args.config_command == "init":
        path = init_config(args.config)
        _emit({"path": str(path), "created": True}, [f"Created {path}"], json_mode=args.json)
    else:
        config = load_config(args.config)
        _validate_config_native_arguments(config)
        _config_warnings(config, _StreamDiagnostics())
        if args.command == "agents":
            _agents(args.json)
        elif args.command == "profiles":
            _profiles(config, args.json)
        elif args.command == "doctor":
            return _doctor(config, args.json, versions=args.versions)
        else:
            _emit(
                {
                    "path": str(config.path),
                    "sources": [str(path) for path in config.sources],
                    "exists": config.exists,
                    "valid": True,
                },
                [
                    f"Valid config: {', '.join(str(path) for path in config.sources)}"
                    if config.exists
                    else f"No config at {config.path}; using built-in defaults."
                ],
                json_mode=args.json,
            )
    return 0


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
    capture = process.capture
    decoded = capture.decoded if isinstance(capture, ConsumedCapture) else None
    stdout: str | None = None
    stderr = ""
    encoding_error: ResultError | None = None
    if decoded is None:
        try:
            stdout = raw_stdout(capture).decode("utf-8")
        except UnicodeDecodeError:
            encoding_error = ResultError("output_encoding", "Agent stdout is not valid UTF-8.")
    try:
        stderr = process.stderr.decode("utf-8")
    except UnicodeDecodeError:
        encoding_error = encoding_error or ResultError(
            "output_encoding", "Agent stderr is not valid UTF-8."
        )
    if encoding_error is not None and process.error is None:
        process = _after_run_failure(process, encoding_error)
    if decoded is None:
        decoded = _decode(resolved, stdout) if stdout is not None else DecodedOutput()
    return process, decoded, stderr


def _presentation_error(error: Exception) -> ResultError:
    return ResultError("output_io_error", f"Cannot write diagnostics to stderr: {error}.")


def _after_run_failure(process: ProcessResult, failure: ResultError) -> ProcessResult:
    cleanup_error = None
    if process.process_group is not None:
        cleanup_error = cleanup_process_group(process.process_group)
    failure = process.error or failure
    if cleanup_error is not None:
        failure = ResultError(
            failure.code, f"{failure.message} Process-group cleanup failed: {cleanup_error}."
        )
    return replace(process, error=failure)


def _after_run_presentation_failure(process: ProcessResult, error: Exception) -> ProcessResult:
    return _after_run_failure(process, _presentation_error(error))


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
        "fast": resolved.options.fast,
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
        fast = "native" if resolved.options.fast is None else str(resolved.options.fast).lower()
        print(f"fast: {fast}")
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


@dataclass(frozen=True)
class _RunPlan:
    resolved: ResolvedProfile
    invocation: Invocation
    cwd: Path
    timeout: float
    consumer: ByteConsumer | None
    json_mode: bool


def _execute(plan: _RunPlan, diagnostics: _Diagnostics) -> tuple[ProcessResult, DecodedOutput]:
    resolved = plan.resolved
    try:
        diagnostics.open()
        diagnostics.line(f"prat: launching {resolved.agent.label}")
        diagnostics.flush()
    except diagnostics.start_errors as error:
        diagnostics.fail()
        return (
            ProcessResult(RawCapture(), b"", None, 0, _presentation_error(error)),
            DecodedOutput(),
        )

    process = run(
        plan.invocation,
        plan.cwd,
        plan.timeout,
        consumer=plan.consumer,
        progress=diagnostics.progress_callback(),
    )
    process, decoded, native_stderr = _decode_process(resolved, process)
    result = normalize(resolved, process, decoded)
    if native_stderr:
        try:
            diagnostics.text(native_stderr + ("" if native_stderr.endswith("\n") else "\n"))
        except diagnostics.write_errors as error:
            process = _after_run_presentation_failure(process, error)
            diagnostics.fail()
    try:
        diagnostics.line(
            f"prat: {resolved.agent.label} finished with status {result.status} "
            f"in {result.duration_ms}ms"
        )
        if not plan.json_mode and result.error is not None:
            diagnostics.line(f"prat: {result.error.message}")
        diagnostics.flush()
    except diagnostics.write_errors as error:
        process = _after_run_presentation_failure(process, error)
        diagnostics.fail()
    return process, decoded


def _run_command(arguments: list[str], invocation_cwd: Path) -> int:
    json_mode = _json_requested(arguments)
    resolved: ResolvedProfile | None = None
    try:
        parsed = _parse_run(arguments)
        json_mode = parsed.json
        with closing(_diagnostics_for(progress=parsed.progress)) as diagnostics:
            config = load_config(parsed.config, cwd=invocation_cwd)
            _validate_config_native_arguments(config)
            _config_warnings(config, diagnostics)
            resolved = resolve_profile(config, parsed.selector, parsed.options)
            label = option_labels(config, parsed.selector, parsed.options).get(
                "native_args", f"selector {parsed.selector!r}.native_args"
            )
            _validate_native_arguments(resolved, label)
            cwd = _run_cwd(parsed.cwd, invocation_cwd)
            prompt = acquire_prompt(parsed.prompt_source, invocation_cwd)
            invocation = _build_invocation(resolved, prompt)
            if parsed.dry_run:
                _preview(resolved, invocation, cwd, json_mode=json_mode)
                return 0
            timeout = resolved.options.timeout
            if timeout is None:
                raise PratError("Resolved timeout is missing.", code="invalid_arguments")
            adapter = ADAPTERS[resolved.agent.name]
            plan = _RunPlan(
                resolved=resolved,
                invocation=invocation,
                cwd=cwd,
                timeout=timeout,
                consumer=adapter.consumer() if adapter.consumer is not None else None,
                json_mode=json_mode,
            )
            process, decoded = _execute(plan, diagnostics)
        result = normalize(resolved, process, decoded)
        payload = result_dict(result)
        _emit_result(payload, json_mode=json_mode)
        return result.exit_code
    except InputInterrupted as error:
        exit_code = SIGNAL_EXIT_BASE + error.signum
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
    return 0


def _silence_broken_stream(name: Literal["stdout", "stderr"]) -> None:
    descriptor = os.open(os.devnull, os.O_WRONLY)
    target: int | None = None
    try:
        try:
            target = getattr(sys, name).fileno()
            os.dup2(descriptor, target)
        except (AttributeError, OSError, ValueError):
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))  # noqa: SIM115
        with suppress(OSError, ValueError):
            getattr(sys, name).flush()
    finally:
        if descriptor != target:
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
        _silence_broken_stream("stdout")
        return 1
    return exit_code
