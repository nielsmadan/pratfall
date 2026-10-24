import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from pratfall.catalog import AGENTS
from pratfall.cli.presentation import _emit, _print_diagnostic
from pratfall.codes import SIGNAL_EXIT_BASE
from pratfall.config import agent_command
from pratfall.interruption import InterruptionState, handler_for, handling
from pratfall.models import AgentSpec, Config, Invocation
from pratfall.runner import ProcessResult, RunLimits, raw_stdout, run

VERSION_TIMEOUT = 3.0
RUN_VERSION_TIMEOUT = 15.0
VERSION_OUTPUT_LIMIT = 64 * 1024
VERSION_LIMITS = RunLimits(stdout=VERSION_OUTPUT_LIMIT, stderr=VERSION_OUTPUT_LIMIT)

Which = Callable[[str], str | None]


@dataclass
class _DoctorState(InterruptionState):
    result_chosen: bool = False


class _DoctorInterrupted(Exception):
    pass


def _doctor_inventory(
    config: Config, which: Which, interruption: InterruptionState | None = None
) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for agent in AGENTS:
        if interruption is not None and interruption.received is not None:
            break
        command = config.commands.get(agent.name, agent.command)
        executable = command[0]
        found = which(executable)
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


def _doctor(
    config: Config,
    json_mode: bool,
    *,
    versions: bool,
    cwd: Path,
    which: Which,
    version_timeout: float = VERSION_TIMEOUT,
) -> int:
    records: list[dict[str, object]] = []
    if not versions:
        records = _doctor_inventory(config, which)
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
                records = _doctor_inventory(config, which, interruption)
                for agent, record in zip(AGENTS, records, strict=True):
                    if interruption.received is not None:
                        break
                    if not record["available"]:
                        continue
                    process, version, version_error = probe_version(
                        agent_command(config, agent), agent, cwd, version_timeout
                    )
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
        _print_diagnostic(message)
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


def probe_version(
    command: tuple[str, ...], agent: AgentSpec, cwd: Path, timeout: float
) -> tuple[ProcessResult, str | None, str | None]:
    process = run(
        Invocation((*command, *agent.version_args), b""), cwd, timeout, limits=VERSION_LIMITS
    )
    version, error = _version_result(process)
    return process, version, error


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
