import argparse
import json
import sys
from collections.abc import Callable
from contextlib import closing, suppress
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

from pratfall.adapters.registry import ADAPTERS
from pratfall.catalog import AGENTS
from pratfall.cli.doctor import _doctor
from pratfall.cli.parsing import _json_requested, _parse_run, _run_cwd
from pratfall.cli.presentation import (
    _config_warnings,
    _Diagnostics,
    _diagnostics_for,
    _emit,
    _emit_result,
    _presentation_error,
    _preview,
    _StdoutLatch,
    _StreamDiagnostics,
)
from pratfall.codes import INTERNAL_ERROR, SIGNAL_EXIT_BASE, exit_code_for
from pratfall.config import config_path, init_config, load_config, option_origins, resolve_profile
from pratfall.consumer import ByteConsumer, CapturingConsumer
from pratfall.errors import PratError
from pratfall.models import (
    Config,
    ConsumedCapture,
    DecodedOutput,
    Invocation,
    OptionOrigin,
    RawCapture,
    ResolvedProfile,
    ResultError,
)
from pratfall.output import extract_code, normalize, result_dict, validation_error
from pratfall.prompt_input import InputInterrupted, acquire_prompt, prepend_contexts
from pratfall.prompt_templates import render_template
from pratfall.runner import ProcessResult, cleanup_process_group, raw_stdout, run


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


def _templates(config: Config, json_mode: bool) -> None:
    records = []
    lines = []
    for name, template in sorted(config.templates.items()):
        records.append({"name": name, "prompt": template.prompt, "source": str(template.source)})
        lines.append(f"{name}: {json.dumps(template.prompt, ensure_ascii=False)}")
    _emit({"templates": records}, lines or ["No templates configured."], json_mode=json_mode)


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
        fallback = OptionOrigin(f"{source}: profiles.{name}.native_args")
        label = option_origins(config, name).get("native_args", fallback).label
        _validate_native_arguments(resolved, label)


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
        elif args.command == "templates":
            _templates(config, args.json)
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


def _build_invocation(resolved: ResolvedProfile, prompt: bytes) -> Invocation:
    return ADAPTERS[resolved.agent.name].build(resolved, prompt)


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


def _guard[T](action: Callable[[], T], recover: Callable[[Exception], T]) -> T:
    try:
        return action()
    except BrokenPipeError:
        raise
    except Exception as error:  # noqa: BLE001 - last resort so no failure escapes as a traceback
        return recover(error)


def _printable(error: Exception) -> str:
    text = _guard(lambda: f"{type(error).__name__}: {error}", lambda _: type(error).__name__)
    return text.encode("utf-8", "backslashreplace").decode("utf-8")


def _internal_message(error: Exception) -> str:
    return f"Unexpected failure: {_printable(error)}"


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


@dataclass(frozen=True)
class _RunPlan:
    resolved: ResolvedProfile
    invocation: Invocation
    cwd: Path
    timeout: float
    consumer: ByteConsumer | None
    json_mode: bool
    trace: bool


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
    return _guard(
        lambda: _present_run(plan, diagnostics, process),
        lambda error: (_after_run_internal_failure(process, error), DecodedOutput()),
    )


def _after_run_internal_failure(process: ProcessResult, error: Exception) -> ProcessResult:
    return _after_run_failure(process, ResultError(INTERNAL_ERROR, _internal_message(error)))


def _present_run(
    plan: _RunPlan, diagnostics: _Diagnostics, process: ProcessResult
) -> tuple[ProcessResult, DecodedOutput]:
    resolved = plan.resolved
    process, decoded, native_stderr = _decode_process(resolved, process)
    result = normalize(resolved, process, decoded)
    native_stdout = _trace_stdout(plan, process)
    if native_stdout:
        try:
            diagnostics.bytes(native_stdout + (b"" if native_stdout.endswith(b"\n") else b"\n"))
        except diagnostics.write_errors as error:
            process = _after_run_presentation_failure(process, error)
            diagnostics.fail()
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


def _trace_stdout(plan: _RunPlan, process: ProcessResult) -> bytes:
    if not plan.trace:
        return b""
    if isinstance(plan.consumer, CapturingConsumer):
        return bytes(plan.consumer.stdout)
    if isinstance(process.capture, RawCapture):
        return process.capture.stdout
    return b""


@dataclass
class _RunState:
    json_mode: bool = False
    stdout: _StdoutLatch = field(default_factory=_StdoutLatch)
    resolved: ResolvedProfile | None = None
    process: ProcessResult | None = None


def _run_command(arguments: list[str], invocation_cwd: Path) -> int:
    state = _RunState(json_mode=_json_requested(arguments))
    return _guard(
        lambda: _run_selected(arguments, invocation_cwd, state),
        lambda error: _internal_failure(error, state),
    )


def _cleanup_after(process: ProcessResult | None) -> None:
    if process is not None and process.process_group is not None:
        cleanup_process_group(process.process_group)


def _internal_failure(error: Exception, state: _RunState) -> int:
    _cleanup_after(state.process)
    message = _internal_message(error)
    with suppress(OSError, ValueError):
        if state.json_mode and not state.stdout.emitted:
            print(json.dumps(_internal_payload(message, state.resolved), ensure_ascii=False))
        else:
            print(f"prat: {message}", file=sys.stderr)
    return exit_code_for(INTERNAL_ERROR)


def _internal_payload(message: str, resolved: ResolvedProfile | None) -> dict[str, object]:
    return validation_error(
        Exception(message),
        INTERNAL_ERROR,
        exit_code_for(INTERNAL_ERROR),
        status="error",
        resolved=resolved,
    )


def _run_selected(arguments: list[str], invocation_cwd: Path, state: _RunState) -> int:
    json_mode = state.json_mode
    resolved: ResolvedProfile | None = None
    try:
        parsed = _parse_run(arguments)
        json_mode = parsed.json
        state.json_mode = json_mode
        with closing(_diagnostics_for(progress=parsed.progress)) as diagnostics:
            config = load_config(parsed.config, cwd=invocation_cwd)
            _validate_config_native_arguments(config)
            _config_warnings(config, diagnostics)
            resolved = resolve_profile(config, parsed.selector, parsed.options)
            state.resolved = resolved
            fallback = OptionOrigin(f"selector {parsed.selector!r}.native_args")
            origins = option_origins(config, parsed.selector, parsed.options)
            label = origins.get("native_args", fallback).label
            _validate_native_arguments(resolved, label)
            cwd = _run_cwd(parsed.cwd, invocation_cwd)
            template = (
                config.templates.get(parsed.template) if parsed.template is not None else None
            )
            if parsed.template is not None and template is None:
                raise PratError(
                    f"Unknown template {parsed.template!r}; run 'prat templates' to list choices.",
                    code="invalid_arguments",
                )
            prompt = acquire_prompt(
                parsed.prompt_source, invocation_cwd, allow_absent=template is not None
            )
            if template is not None:
                prompt = render_template(template.prompt, prompt)
            prompt = prepend_contexts(parsed.contexts, prompt, invocation_cwd)
            invocation = _build_invocation(resolved, prompt)
            if parsed.dry_run:
                _preview(resolved, invocation, cwd, state.stdout, json_mode=json_mode)
                return 0
            timeout = resolved.options.timeout
            if timeout is None:
                raise PratError("Resolved timeout is missing.", code="invalid_arguments")
            adapter = ADAPTERS[resolved.agent.name]
            consumer = adapter.consumer() if adapter.consumer is not None else None
            if parsed.trace and consumer is not None:
                consumer = CapturingConsumer(consumer)
            plan = _RunPlan(
                resolved=resolved,
                invocation=invocation,
                cwd=cwd,
                timeout=timeout,
                consumer=consumer,
                json_mode=json_mode,
                trace=parsed.trace,
            )
            process, decoded = _execute(plan, diagnostics)
            state.process = process
        result = normalize(resolved, process, decoded)
        if parsed.extract:
            result = replace(result, output=extract_code(result.output))
        payload = result_dict(result)
        _emit_result(payload, state.stdout, json_mode=json_mode)
        return result.exit_code
    except InputInterrupted as error:
        exit_code = SIGNAL_EXIT_BASE + error.signum
        payload = validation_error(
            error, "interrupted", exit_code, status="interrupted", resolved=resolved
        )
        if json_mode:
            print(json.dumps(payload, ensure_ascii=False))
        else:
            print(f"prat: {error}", file=sys.stderr)
        return exit_code
    except PratError as error:
        payload = validation_error(
            error, error.code, error.exit_code, status="error", resolved=resolved
        )
        if json_mode:
            print(json.dumps(payload, ensure_ascii=False))
        else:
            print(f"prat: {error}", file=sys.stderr)
        return error.exit_code
