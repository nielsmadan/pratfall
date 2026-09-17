import re
from dataclasses import asdict

from pratfall.codes import FAILURE_EXIT, SIGNAL_EXIT_BASE, TIMEOUT_EXIT, Code, exit_code_for
from pratfall.models import DecodedOutput, NormalizedResult, ResolvedProfile, ResultError
from pratfall.runner import ProcessResult

_LINES = re.compile(r"[^\r\n]*(?:\r\n?|\n|$)")
_FENCE = re.compile(r" {0,3}(`{3,}|~{3,})(.*)")


def extract_code(output: str) -> str:
    opening: str | None = None
    body_start = 0
    for line in _LINES.finditer(output):
        fence = _FENCE.fullmatch(line.group().rstrip("\r\n"))
        if fence is None:
            continue
        marker, info = fence.groups()
        if opening is None:
            if marker[0] == "`" and "`" in info:
                continue
            opening = marker
            body_start = line.end()
        elif marker[0] == opening[0] and len(marker) >= len(opening) and not info.strip(" \t"):
            return output[body_start : line.start()]
    return output


def normalize(
    resolved: ResolvedProfile,
    process: ProcessResult,
    decoded: DecodedOutput,
) -> NormalizedResult:
    if process.interrupted_by is not None:
        return _result(
            resolved,
            process,
            decoded,
            "interrupted",
            SIGNAL_EXIT_BASE + process.interrupted_by,
            process.error,
        )
    if process.timed_out:
        error = process.error or ResultError("timeout", "The agent timed out.")
        return _result(resolved, process, decoded, "timeout", TIMEOUT_EXIT, error)
    if process.error is not None:
        return _result(
            resolved, process, decoded, "error", exit_code_for(process.error.code), process.error
        )
    if decoded.timed_out:
        error = decoded.error or ResultError("timeout", "The agent timed out.")
        return _result(resolved, process, decoded, "timeout", TIMEOUT_EXIT, error)
    native_exit = process.native_exit_code
    if native_exit is not None and native_exit < 0:
        signum = -native_exit
        return _result(
            resolved,
            process,
            decoded,
            "interrupted",
            SIGNAL_EXIT_BASE + signum,
            ResultError("native_signal", f"{resolved.agent.label} died from signal {signum}."),
        )
    if native_exit not in {None, 0}:
        return _result(
            resolved,
            process,
            decoded,
            "error",
            native_exit,
            ResultError("native_exit", f"{resolved.agent.label} exited with status {native_exit}."),
        )
    if decoded.error is not None:
        return _result(resolved, process, decoded, "error", FAILURE_EXIT, decoded.error)
    return _result(resolved, process, decoded, "success", 0, None)


def _result(
    resolved: ResolvedProfile,
    process: ProcessResult,
    decoded: DecodedOutput,
    status: str,
    exit_code: int,
    error: ResultError | None,
) -> NormalizedResult:
    return NormalizedResult(
        agent=resolved.agent.name,
        profile=resolved.profile,
        model=resolved.options.model,
        reported_models=decoded.reported_models,
        cost_usd=decoded.cost_usd,
        status=status,
        output=decoded.output,
        exit_code=exit_code,
        native_exit_code=process.native_exit_code,
        duration_ms=process.duration_ms,
        usage=decoded.usage,
        error=error,
    )


def result_dict(result: NormalizedResult) -> dict[str, object]:
    return {"schema_version": 1, **asdict(result)}


def validation_error(
    error: Exception,
    code: Code,
    exit_code: int,
    *,
    status: str,
    resolved: ResolvedProfile | None,
) -> dict[str, object]:
    return result_dict(
        NormalizedResult(
            agent=None if resolved is None else resolved.agent.name,
            profile=None if resolved is None else resolved.profile,
            model=None if resolved is None else resolved.options.model,
            reported_models=None,
            cost_usd=None,
            status=status,
            output="",
            exit_code=exit_code,
            native_exit_code=None,
            duration_ms=0,
            usage=None,
            error=ResultError(code, str(error)),
        )
    )
