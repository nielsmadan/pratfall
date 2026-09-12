from dataclasses import asdict

from pratfall.models import DecodedOutput, NormalizedResult, ResolvedProfile, ResultError
from pratfall.runner import ProcessResult


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
            128 + process.interrupted_by,
            process.error,
        )
    if process.timed_out:
        error = process.error or ResultError("timeout", "The agent timed out.")
        return _result(resolved, process, decoded, "timeout", 124, error)
    if process.error is not None:
        return _result(
            resolved, process, decoded, "error", _runner_exit(process.error.code), process.error
        )
    if decoded.timed_out:
        error = decoded.error or ResultError("timeout", "The agent timed out.")
        return _result(resolved, process, decoded, "timeout", 124, error)
    native_exit = process.native_exit_code
    if native_exit is not None and native_exit < 0:
        signum = -native_exit
        return _result(
            resolved,
            process,
            decoded,
            "interrupted",
            128 + signum,
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
        return _result(resolved, process, decoded, "error", 1, decoded.error)
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


def validation_error(error: Exception, code: str, exit_code: int) -> dict[str, object]:
    return {
        "schema_version": 1,
        "agent": None,
        "profile": None,
        "model": None,
        "reported_models": None,
        "cost_usd": None,
        "status": "error",
        "output": "",
        "exit_code": exit_code,
        "native_exit_code": None,
        "duration_ms": 0,
        "usage": None,
        "error": {"code": code, "message": str(error)},
    }


def _runner_exit(code: str) -> int:
    if code == "executable_not_found":
        return 127
    if code == "executable_not_executable":
        return 126
    return 1
