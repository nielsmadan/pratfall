import argparse
import json
import os
import sys
from collections.abc import Callable, Mapping
from contextlib import ExitStack, closing, suppress
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import BinaryIO

from pratfall.adapters.registry import ADAPTERS
from pratfall.catalog import AGENTS
from pratfall.cli.doctor import _doctor
from pratfall.cli.parsing import RunArguments, _json_requested, _parse_run, _run_cwd
from pratfall.cli.presentation import (
    _config_warnings,
    _Diagnostics,
    _diagnostics_for,
    _emit,
    _emit_result,
    _guard,
    _internal_message,
    _limit_text,
    _presentation_error,
    _preview,
    _stderr_is_tty,
    _StdoutLatch,
    _StreamDiagnostics,
)
from pratfall.cli.shell_completion import _completion_script
from pratfall.cli.workflow_run import run_workflow
from pratfall.codes import INTERNAL_ERROR, SIGNAL_EXIT_BASE, exit_code_for
from pratfall.config import config_path, init_config, load_config, option_origins, resolve_profile
from pratfall.consumer import ByteConsumer, CapturingConsumer
from pratfall.errors import PratError
from pratfall.instructions import read_instructions
from pratfall.limits import DEFAULT_MAX_PARALLEL, WORKFLOW_SPEC_BYTES
from pratfall.models import (
    Config,
    ConsumedCapture,
    DecodedOutput,
    Invocation,
    NormalizedResult,
    OptionOrigin,
    RawCapture,
    ResolvedProfile,
    ResultError,
    Workflow,
)
from pratfall.notify import encode, notification_for, send, should_notify
from pratfall.option_paths import prepare_attachments, prepare_directories, resolve_path
from pratfall.output import extract_code, normalize, result_dict, validation_error
from pratfall.prompt_editor import edit_prompt
from pratfall.prompt_input import InputInterrupted, PromptSource, acquire_prompt, prepend_contexts
from pratfall.prompt_templates import (
    BoundInput,
    bind_run_input,
    render_template,
    template_parameters,
)
from pratfall.runner import ProcessResult, cleanup_process_group, raw_stdout, run
from pratfall.schema import prepare_schema
from pratfall.workflow_plan import workflow_parameter_names


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
            "add_dirs": caps.add_dirs,
            "attachments": caps.attachments,
            "attachment_types": list(caps.attachment_types) or None,
            "attachment_max_count": caps.attachment_max_count,
            "instructions": caps.instructions,
            "schema": caps.schema,
            "native_agent": caps.native_agent,
            "session_id": caps.session_id,
            "reports_session_id": caps.reports_session_id,
            "tools": caps.tools,
            "disabled_tools": caps.disabled_tools,
            "tools_empty": caps.tools_empty,
            "tools_scope": caps.tools_scope,
            "disabled_tools_scope": caps.disabled_tools_scope,
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
        supported = [
            field
            for field in (
                "model",
                "effort",
                "fast",
                "add_dirs",
                "attachments",
                "instructions",
                "schema",
                "tools",
                "disabled_tools",
                "native_agent",
                "session_id",
            )
            if getattr(caps, field)
        ]
        supported.extend(sorted(caps.budgets))
        if caps.attachments:
            supported.append("attachment_types=" + "/".join(caps.attachment_types))
            if caps.attachment_max_count is not None:
                supported.append(f"attachment_max_count={caps.attachment_max_count}")
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
        template = config.profiles[name].prompt
        parameters = () if template is None else template_parameters(template.prompt)
        records.append(
            {
                "name": name,
                "agent": resolved.agent.name,
                "options": options,
                "prompt": None if template is None else template.prompt,
                "prompt_source": None if template is None else str(template.source),
                "parameters": list(parameters),
            }
        )
        description = f"{name}: {resolved.agent.name}"
        for key, value in options.items():
            if value is None or (value == () and key != "tools"):
                continue
            rendered = (
                json.dumps(value, ensure_ascii=False)
                if key
                in {
                    "native_args",
                    "command_prefix",
                    "add_dirs",
                    "attachments",
                    "instructions",
                    "schema",
                    "instructions_file",
                    "tools",
                    "disabled_tools",
                    "native_agent",
                    "session_id",
                }
                else value
            )
            description += f" {key}={rendered}"
        if template is not None:
            description += f" prompt={json.dumps(template.prompt, ensure_ascii=False)}"
        if parameters:
            description += f" parameters={','.join(parameters)}"
        lines.append(description)
    _emit({"profiles": records}, lines or ["No profiles configured."], json_mode=json_mode)


def _templates(config: Config, json_mode: bool) -> None:
    records = []
    lines = []
    for name, template in sorted(config.templates.items()):
        parameters = template_parameters(template.prompt)
        records.append(
            {
                "name": name,
                "prompt": template.prompt,
                "source": str(template.source),
                "parameters": list(parameters),
            }
        )
        line = f"{name}: {json.dumps(template.prompt, ensure_ascii=False)}"
        lines.append(line + (f" parameters={','.join(parameters)}" if parameters else ""))
    _emit({"templates": records}, lines or ["No templates configured."], json_mode=json_mode)


def _workflows(config: Config, json_mode: bool) -> None:
    records = []
    lines = []
    for name, workflow in sorted(config.workflows.items()):
        parameters = workflow_parameter_names(config, workflow)
        records.append(
            {
                "name": name,
                "parallel": workflow.parallel,
                "parameters": list(parameters),
                "source": str(workflow.source),
                "steps": [
                    {
                        "name": step.name,
                        "profile": step.selector,
                        "after": list(step.after),
                        "input": step.input,
                        "bind": dict(step.bind),
                        "each": step.each,
                        "split": None if step.each is None else step.split,
                        "as": step.as_parameter,
                        "max_parallel": None if step.each is None else step.max_parallel,
                        "max_items": None if step.each is None else step.max_items,
                        "overflow": None if step.each is None else step.overflow,
                    }
                    for step in workflow.steps
                ],
            }
        )
        steps = "; ".join(_workflow_step_text(workflow, i) for i in range(len(workflow.steps)))
        line = f"{name}: {steps}"
        line += "" if workflow.parallel else " parallel=false"
        line += f" parameters={','.join(parameters)}" if parameters else ""
        lines.append(f"{line} source={workflow.source}")
    _emit({"workflows": records}, lines or ["No workflows configured."], json_mode=json_mode)


def _workflow_step_text(workflow: Workflow, index: int) -> str:
    step = workflow.steps[index]
    default = (workflow.steps[index - 1].name,) if index else ()
    text = f"{step.name}={step.selector}"
    if step.after != default:
        text += f" after={','.join(step.after) or 'none'}"
    if step.input == "none":
        text += " input=none"
    if step.bind:
        text += " bind=" + ",".join(f"{key}:{source}" for key, source in step.bind.items())
    if step.each is not None:
        text += f" each={step.each}"
        text += "" if step.split == "lines" else f" split={step.split}"
        text += "" if step.as_parameter is None else f" as={step.as_parameter}"
        text += (
            ""
            if step.max_parallel == DEFAULT_MAX_PARALLEL
            else f" max_parallel={step.max_parallel}"
        )
        text += _limit_text(step)
    return text


def _validate_native_arguments(
    resolved: ResolvedProfile, label: str, origins: Mapping[str, OptionOrigin]
) -> None:
    adapter = ADAPTERS.get(resolved.agent.name)
    if adapter is None:
        return
    try:
        adapter.validate(resolved)
    except PratError as error:
        origin = origins.get(error.option) if error.option is not None else None
        raise PratError(
            f"{origin.label if origin is not None else label}: {error}",
            code=origin.code if origin is not None else error.code,
        ) from error


def _validate_config_native_arguments(config: Config) -> None:
    for name in config.profiles:
        resolved = resolve_profile(config, name)
        source = config.profiles[name].source or config.path
        fallback = OptionOrigin(f"{source}: profiles.{name}.native_args")
        label = option_origins(config, name).get("native_args", fallback).label
        _validate_native_arguments(resolved, label, option_origins(config, name))


def _dispatch(args: argparse.Namespace) -> int:
    if args.command == "completion":
        if args.json:
            raise PratError(
                "completion prints shell code; --json is not supported.", code="invalid_arguments"
            )
        sys.stdout.write(_completion_script(args.shell, os.environ))
    elif args.command == "config" and args.config_command == "path":
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
        elif args.command == "workflows":
            _workflows(config, args.json)
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
    return (
        ADAPTERS[resolved.agent.name].for_schema(resolved.options.schema is not None).decode(stdout)
    )


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
    if resolved.options.schema is not None and decoded.error is not None:
        cleanup_error = (
            cleanup_process_group(process.process_group)
            if process.process_group is not None
            else None
        )
        if cleanup_error is not None:
            stderr += f"\nprat: Process-group cleanup failed: {cleanup_error}.\n"
            failure = decoded.error
            decoded = replace(
                decoded,
                error=ResultError(
                    failure.code,
                    f"{failure.message} Process-group cleanup failed: {cleanup_error}.",
                ),
            )
    return process, decoded, stderr


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


@dataclass(frozen=True)
class _StepRun:
    input: str | None


def _run_command(arguments: list[str], invocation_cwd: Path, step: _StepRun | None = None) -> int:
    state = _RunState(json_mode=step is not None or _json_requested(arguments))
    return _guard(
        lambda: _run_selected(arguments, invocation_cwd, state, step),
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


def _resolve_run_paths(parsed: RunArguments, invocation_cwd: Path) -> RunArguments:
    options = parsed.options
    if options.add_dirs is not None:
        origin = OptionOrigin("command line: --add-dir", "invalid_arguments")
        paths = tuple(resolve_path(value, invocation_cwd, origin) for value in options.add_dirs)
        options = replace(options, add_dirs=paths)
    if options.attachments is not None:
        origin = OptionOrigin("command line: --attach", "invalid_arguments")
        paths = tuple(resolve_path(value, invocation_cwd, origin) for value in options.attachments)
        options = replace(options, attachments=paths)
    if options.instructions_file is not None:
        origin = OptionOrigin("command line: --instructions-file", "invalid_arguments")
        path = resolve_path(options.instructions_file, invocation_cwd, origin)
        options = replace(options, instructions_file=path)
    if options.schema is not None:
        origin = OptionOrigin("command line: --schema", "invalid_arguments")
        options = replace(options, schema=resolve_path(options.schema, invocation_cwd, origin))
    return replace(parsed, options=options)


def _prepare_run_instructions(
    resolved: ResolvedProfile, origins: Mapping[str, OptionOrigin]
) -> ResolvedProfile:
    if resolved.options.instructions_file is None:
        return resolved
    text = read_instructions(
        resolved.options.instructions_file,
        origins.get("instructions_file", OptionOrigin("instructions_file")),
    )
    return replace(
        resolved, options=replace(resolved.options, instructions=text, instructions_file=None)
    )


def _prepare_run_paths(
    resolved: ResolvedProfile, origins: Mapping[str, OptionOrigin], label: str
) -> ResolvedProfile:
    if resolved.options.add_dirs:
        paths = prepare_directories(
            resolved.options.add_dirs,
            origins.get("add_dirs", OptionOrigin("add_dirs", "invalid_arguments")),
        )
        resolved = replace(resolved, options=replace(resolved.options, add_dirs=paths))
    _validate_native_arguments(resolved, label, origins)
    if not resolved.options.attachments:
        return resolved
    paths = prepare_attachments(
        resolved.options.attachments,
        origins.get("attachments", OptionOrigin("attachments", "invalid_arguments")),
    )
    resolved = replace(resolved, options=replace(resolved.options, attachments=paths))
    _validate_native_arguments(resolved, label, origins)
    return resolved


def _validate_schema_extraction(resolved: ResolvedProfile, extract: bool) -> None:
    if resolved.options.schema is not None and extract:
        raise PratError("--schema and --extract cannot be used together.", code="invalid_arguments")


def _prepare_run_schema(
    resolved: ResolvedProfile, origins: Mapping[str, OptionOrigin], resources: ExitStack
) -> ResolvedProfile:
    if resolved.options.schema is None:
        return resolved
    adapter = ADAPTERS[resolved.agent.name]
    transport = adapter.schema_transport
    if transport is None:
        raise PratError("Agent does not support schema output.", code="invalid_arguments")
    origin = origins.get("schema", OptionOrigin("schema"))
    prepared = resources.enter_context(prepare_schema(resolved.options.schema, origin, transport))
    if adapter.validate_schema is not None:
        try:
            adapter.validate_schema(prepared)
        except PratError as error:
            raise PratError(f"{origin.label}: {error}", code=origin.code) from error
    return replace(resolved, prepared_schema=prepared)


def _notify_after_run(resolved: ResolvedProfile, result: NormalizedResult) -> None:
    if not should_notify(
        resolved.options, result.status, result.duration_ms, stderr_is_tty=_stderr_is_tty()
    ):
        return
    notification = notification_for(resolved.agent.label, result.status, result.duration_ms)
    method = resolved.options.notify_method or "auto"
    send(encode(notification, method, os.environ))


def _finish_run(resolved: ResolvedProfile, result: NormalizedResult, state: _RunState) -> None:
    payload = result_dict(result)
    _emit_result(payload, state.stdout, json_mode=state.json_mode)
    _guard(lambda: _notify_after_run(resolved, result), lambda _: None)


def _active_template(config: Config, parsed: RunArguments) -> tuple[str, str] | None:
    if parsed.template is not None:
        template = config.templates.get(parsed.template)
        if template is None:
            raise PratError(
                f"Unknown template {parsed.template!r}; run 'prat templates' to list choices.",
                code="invalid_arguments",
            )
        return template.prompt, f"template '{parsed.template}'"
    profile = config.profiles.get(parsed.selector)
    if profile is None or profile.prompt is None:
        return None
    return profile.prompt.prompt, f"profile '{parsed.selector}' prompt"


def _bind_run_input(config: Config, parsed: RunArguments) -> tuple[str | None, BoundInput]:
    active = _active_template(config, parsed)
    template, label = active if active is not None else (None, "")
    bound = bind_run_input(template, label, parsed.words, parsed.parameters, parsed.prompt_source)
    return template, bound


def _render_run_prompt(
    parsed: RunArguments, template: str | None, bound: BoundInput, invocation_cwd: Path
) -> bytes:
    prompt = acquire_prompt(
        bound.source,
        invocation_cwd,
        allow_absent=template is not None or parsed.edit,
        allow_blank=parsed.edit,
    )
    if template is None:
        return prompt
    return render_template(template, prompt, parameters=bound.parameters, allow_blank=parsed.edit)


def _with_step(parsed: RunArguments, step: _StepRun | None) -> RunArguments:
    if step is None:
        return parsed
    parsed = replace(parsed, json=True, options=replace(parsed.options, notify=False))
    if step.input is None:
        return parsed
    if parsed.prompt_source is not None:
        raise PratError("Provide exactly one prompt source.", code="invalid_arguments")
    return replace(parsed, prompt_source=PromptSource("inline", step.input))


def _run_selected(
    arguments: list[str], invocation_cwd: Path, state: _RunState, step: _StepRun | None = None
) -> int:
    resolved: ResolvedProfile | None = None
    try:
        parsed = _with_step(_parse_run(arguments), step)
        state.json_mode = parsed.json
        with (
            ExitStack() as resources,
            closing(_diagnostics_for(progress=parsed.progress)) as diagnostics,
        ):
            parsed = _resolve_run_paths(parsed, invocation_cwd)
            config = load_config(parsed.config, cwd=invocation_cwd)
            _validate_config_native_arguments(config)
            if step is None:
                _config_warnings(config, diagnostics)
                if parsed.selector in config.workflows:
                    diagnostics.close()
                    return run_workflow(arguments, parsed, config, invocation_cwd, state.stdout)
            resolved = resolve_profile(config, parsed.selector, parsed.options)
            state.resolved = resolved
            _validate_schema_extraction(resolved, parsed.extract)
            fallback = OptionOrigin(f"selector {parsed.selector!r}.native_args")
            origins = option_origins(config, parsed.selector, parsed.options)
            label = origins.get("native_args", fallback).label
            resolved = _prepare_run_paths(resolved, origins, label)
            cwd = _run_cwd(parsed.cwd, invocation_cwd)
            template, bound = _bind_run_input(config, parsed)
            resolved = _prepare_run_schema(resolved, origins, resources)
            resolved = _prepare_run_instructions(resolved, origins)
            prompt = _render_run_prompt(parsed, template, bound, invocation_cwd)
            prompt = prepend_contexts(parsed.contexts, prompt, invocation_cwd)
            if parsed.edit:
                prompt = edit_prompt(prompt, invocation_cwd)
            invocation = _build_invocation(resolved, prompt)
            if parsed.dry_run:
                _preview(resolved, invocation, cwd, state.stdout, json_mode=state.json_mode)
                return 0
            timeout = resolved.options.timeout
            if timeout is None:
                raise PratError("Resolved timeout is missing.", code="invalid_arguments")
            adapter = ADAPTERS[resolved.agent.name].for_schema(resolved.options.schema is not None)
            consumer = adapter.consumer() if adapter.consumer is not None else None
            if parsed.trace and consumer is not None:
                consumer = CapturingConsumer(consumer)
            plan = _RunPlan(
                resolved=resolved,
                invocation=invocation,
                cwd=cwd,
                timeout=timeout,
                consumer=consumer,
                json_mode=state.json_mode,
                trace=parsed.trace,
            )
            process, decoded = _execute(plan, diagnostics)
            state.process = process
        result = normalize(resolved, process, decoded)
        if parsed.extract:
            result = replace(result, output=extract_code(result.output))
        _finish_run(resolved, result, state)
        return result.exit_code
    except InputInterrupted as error:
        exit_code = SIGNAL_EXIT_BASE + error.signum
        payload = validation_error(
            error, "interrupted", exit_code, status="interrupted", resolved=resolved
        )
        if state.json_mode:
            print(json.dumps(payload, ensure_ascii=False))
        else:
            print(f"prat: {error}", file=sys.stderr)
        return exit_code
    except PratError as error:
        payload = validation_error(
            error, error.code, error.exit_code, status="error", resolved=resolved
        )
        if state.json_mode:
            print(json.dumps(payload, ensure_ascii=False))
        else:
            print(f"prat: {error}", file=sys.stderr)
        return error.exit_code


def _read_step_spec(stdin: BinaryIO) -> tuple[list[str], _StepRun, int]:
    chunks: list[bytes] = []
    size = 0
    while size <= WORKFLOW_SPEC_BYTES:
        try:
            chunk = stdin.read(min(65536, WORKFLOW_SPEC_BYTES + 1 - size))
        except (OSError, ValueError) as error:
            raise PratError(
                f"Cannot read workflow step spec: {error}.", code="invalid_arguments"
            ) from error
        if not chunk:
            break
        chunks.append(chunk)
        size += len(chunk)
    if size > WORKFLOW_SPEC_BYTES:
        raise PratError(
            f"Workflow step spec exceeds the {WORKFLOW_SPEC_BYTES} byte limit.",
            code="invalid_arguments",
        )
    try:
        spec = json.loads(b"".join(chunks).decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError) as error:
        raise PratError(
            f"Workflow step spec is not valid JSON: {error}.", code="invalid_arguments"
        ) from error
    return _validated_step_spec(spec)


def _validated_step_spec(spec: object) -> tuple[list[str], _StepRun, int]:
    if not isinstance(spec, dict):
        raise PratError("Workflow step spec must be a JSON object.", code="invalid_arguments")
    version = spec.get("version")
    if type(version) is not int or version != 1:
        raise PratError("Workflow step spec version must be 1.", code="invalid_arguments")
    arguments = spec.get("arguments")
    if not isinstance(arguments, list) or not all(isinstance(item, str) for item in arguments):
        raise PratError(
            "Workflow step spec arguments must be a list of strings.", code="invalid_arguments"
        )
    parent = spec.get("parent")
    if type(parent) is not int:
        raise PratError("Workflow step spec parent must be an integer.", code="invalid_arguments")
    if "input" not in spec:
        raise PratError("Workflow step spec must include input.", code="invalid_arguments")
    text = spec["input"]
    if text is not None and not isinstance(text, str):
        raise PratError(
            "Workflow step spec input must be a string or null.", code="invalid_arguments"
        )
    return list(arguments), _StepRun(text), parent


def _step_failure(error: PratError) -> int:
    payload = validation_error(error, error.code, error.exit_code, status="error", resolved=None)
    print(json.dumps(payload, ensure_ascii=False))
    return error.exit_code


def _step_command(
    stdin: BinaryIO,
    invocation_cwd: Path,
    watch_parent: Callable[[int], None] = lambda _: None,
) -> int:
    try:
        arguments, step, parent = _read_step_spec(stdin)
    except PratError as error:
        return _step_failure(error)
    watch_parent(parent)
    previous = sys.stdin
    sys.stdin = None
    try:
        return _run_command(arguments, invocation_cwd, step)
    finally:
        sys.stdin = previous
