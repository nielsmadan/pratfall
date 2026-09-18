import json
import os
import signal
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Literal

import pytest

from pratfall import schema as schema_module
from pratfall.adapters import claude, codex, qwen
from pratfall.adapters.registry import ADAPTERS
from pratfall.catalog import BY_NAME
from pratfall.cli import dispatch, main
from pratfall.config import config_path, load_config, resolve_profile
from pratfall.consumer import ConsumerFailure, ConsumerLimits, Retention, StateBudget
from pratfall.errors import PratError
from pratfall.limits import JSON_DEPTH, SCHEMA_BYTES
from pratfall.models import (
    ConsumedCapture,
    DecodedOutput,
    Invocation,
    JsonValue,
    OptionOrigin,
    Options,
    PreparedSchema,
    RawCapture,
    ResolvedProfile,
    ResultError,
)
from pratfall.output import normalize, result_dict
from pratfall.prompt_input import InputInterrupted
from pratfall.runner import ProcessResult
from pratfall.schema import encode_json, parse_json, prepare_schema, retain_answer

ORIGIN = OptionOrigin("test.toml: defaults.schema")


@pytest.mark.parametrize("text", ["{}", "true", "false", '{"type":"array"}'])
def test_schema_reads_valid_schema_without_dialect_restriction(tmp_path: Path, text: str) -> None:
    path = tmp_path / "schema.json"
    path.write_text(text)
    with prepare_schema(str(path), ORIGIN, "inline") as prepared:
        assert prepared.text == text
        assert prepared.value == json.loads(text)
        assert prepared.path is None


@pytest.mark.parametrize(
    "data, message",
    [
        (b'{"type":"object","type":"array"}', "duplicate"),
        (b'{"enum":[NaN]}', "nonfinite"),
        (b'{"enum":[Infinity]}', "nonfinite"),
        (b'{"enum":[1e999]}', "nonfinite"),
        (b'{"enum":[' + b"1" * 129 + b"]}", "numeric"),
        (b'{"enum":[0.' + b"1" * 128 + b"]}", "numeric"),
        (b'{"title":"\\ud800"}', "surrogate"),
        (b'{"\\udfff":true}', "surrogate"),
        (b"\xff", "utf-8"),
        (b'{"a":' + b"[" * JSON_DEPTH + b"0" + b"]" * JSON_DEPTH + b"}", "nesting"),
        (b'{"a":' + b"[" * 2000 + b"0" + b"]" * 2000 + b"}", "nesting"),
        (b"{}" + b" " * SCHEMA_BYTES, "byte limit"),
        (b"[]", "object or boolean"),
        (b"null", "object or boolean"),
        (b'"object"', "object or boolean"),
        (b"{", "property name"),
    ],
)
def test_schema_strict_input_guards(tmp_path: Path, data: bytes, message: str) -> None:
    path = tmp_path / "schema"
    path.write_bytes(data)
    with (
        pytest.raises(PratError, match=message) as caught,
        prepare_schema(str(path), ORIGIN, "inline"),
    ):
        pytest.fail("invalid schema accepted")
    assert caught.value.code == "invalid_config"
    assert str(caught.value).startswith(ORIGIN.label)


@pytest.mark.parametrize("kind", ["missing", "directory", "fifo"])
def test_schema_requires_selected_regular_file(tmp_path: Path, kind: str) -> None:
    path = tmp_path / kind
    if kind == "directory":
        path.mkdir()
    elif kind == "fifo":
        os.mkfifo(path)
    with pytest.raises(PratError), prepare_schema(str(path), ORIGIN, "inline"):
        pytest.fail("nonregular schema accepted")


def test_schema_read_stops_at_limit_plus_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "schema"
    path.write_bytes(b"{}" + b" " * (SCHEMA_BYTES * 2))
    read = os.read
    total = 0

    def measure(fd: int, count: int) -> bytes:
        nonlocal total
        chunk = read(fd, count)
        total += len(chunk)
        return chunk

    monkeypatch.setattr(os, "read", measure)
    with pytest.raises(PratError, match="byte limit"), prepare_schema(str(path), ORIGIN, "inline"):
        pytest.fail("oversized input accepted")
    assert total == SCHEMA_BYTES + 1


@pytest.mark.parametrize("failure", [False, True])
def test_snapshot_lifetime_bytes_permissions_and_exception_propagation(
    tmp_path: Path, failure: bool
) -> None:
    source = tmp_path / "schema"
    data = b'{ "type": "object" }\r\n'
    source.write_bytes(data)
    snapshot: Path | None = None
    exception = OSError("execution body failed")
    try:
        with prepare_schema(str(source), ORIGIN, "file") as prepared:
            assert prepared.path is not None
            snapshot = Path(prepared.path)
            assert snapshot.read_bytes() == data
            assert snapshot.stat().st_mode & 0o777 == 0o600
            assert snapshot.parent.stat().st_mode & 0o777 == 0o700
            source.write_text("false")
            assert snapshot.read_bytes() == data
            if failure:
                raise exception
    except OSError as error:
        assert failure and error is exception
    assert snapshot is not None
    assert not snapshot.parent.exists()


@pytest.mark.parametrize(
    "flags, message",
    [
        (["gemini", "--schema", "missing"], "does not support schema"),
        (["claude", "--schema", "missing", "--extract"], "cannot be used together"),
        (["claude", "--schema", "missing", "--", "--json-schema={}"], "controlled by prat"),
        (["claude", "--schema", "missing"], "Cannot open schema file"),
    ],
)
def test_schema_validation_precedes_prompt_and_editor(
    flags: list[str],
    message: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def forbidden(*args: object, **kwargs: object) -> bytes:
        pytest.fail("prompt or editor acquired before validation")

    monkeypatch.setattr(dispatch, "acquire_prompt", forbidden)
    monkeypatch.setattr(dispatch, "edit_prompt", forbidden)
    assert main(["--json", "--edit", *flags]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["error"]["code"] == "invalid_arguments"
    assert message in result["error"]["message"]
    assert result["structured_output"] is None


def test_schema_inheritance_listing_override_and_extract_rejection(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    global_path = config_path()
    global_path.parent.mkdir(parents=True)
    global_path.write_text('version=1\n[defaults]\nschema="global.json"\n')
    local = tmp_path / ".pratfile"
    local.write_text('version=1\n[profiles.work]\nagent="claude"\nschema="missing.json"\n')
    assert main(["config", "validate"]) == 0
    capsys.readouterr()
    assert main(["profiles", "--json"]) == 0
    record = json.loads(capsys.readouterr().out)["profiles"][0]
    assert record["options"]["schema"] == str(tmp_path / "missing.json")
    assert load_config().defaults.schema == str(global_path.parent / "global.json")
    assert main(["work", "--extract", "task", "--json"]) == 2
    assert "cannot be used together" in json.loads(capsys.readouterr().out)["error"]["message"]
    schema = tmp_path / "chosen.json"
    schema.write_text('{"type":"object"}')
    assert main(["work", "--schema", str(schema), "task", "--dry-run", "--json"]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["schema"] == str(schema)
    assert preview["schema_transport"] == "inline JSON"
    assert preview["argv"][-2:] == ["--json-schema", schema.read_text()]
    assert main(["work", "task", "--dry-run", "--json"]) == 2
    error = json.loads(capsys.readouterr().out)["error"]
    assert error["code"] == "invalid_config"
    assert f"{local}: profiles.work.schema" in error["message"]


def test_schema_config_layer_origins_and_priority(tmp_path: Path) -> None:
    global_path = config_path()
    global_path.parent.mkdir(parents=True)
    global_path.write_text('version=1\n[defaults]\nschema="global"\n')
    local = tmp_path / "nested" / "local.toml"
    local.parent.mkdir()
    local.write_text(
        'version=1\n[defaults]\nschema="local"\n[profiles.work]\nagent="claude"\nschema="profile"\n'
    )
    config = load_config(local)
    assert config.defaults.schema == str(local.parent / "local")
    assert resolve_profile(config, "work").options.schema == str(local.parent / "profile")
    assert resolve_profile(config, "work", Options(schema="cli")).options.schema == "cli"


def test_schema_symlink_parent_and_independent_run_cwd(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "actual" / "child"
    target.mkdir(parents=True)
    (tmp_path / "link").symlink_to(target, target_is_directory=True)
    (target.parent / "schema").write_text('{"title":"actual"}')
    (tmp_path / "schema").write_text('{"title":"wrong"}')
    assert (
        main(
            [
                "cc",
                "--schema",
                "link/../schema",
                "--cwd",
                str(target),
                "task",
                "--dry-run",
                "--json",
            ]
        )
        == 0
    )
    preview = json.loads(capsys.readouterr().out)
    assert preview["schema"] == str(tmp_path / "link" / ".." / "schema")
    assert preview["argv"][-1] == '{"title":"actual"}'


def test_schema_native_only_passthrough_preserved() -> None:
    resolved = ResolvedProfile(
        BY_NAME["claude"], None, ("fake",), Options(native_args=("--json-schema={}",))
    )
    ADAPTERS["claude"].validate(resolved)
    assert claude.build(resolved, b"task").argv[-1] == "--json-schema={}"
    for native in [("--json-schema", "{}"), ("--json-schema={}",)]:
        with pytest.raises(PratError, match="controlled by prat"):
            claude.validate(
                replace(resolved, options=Options(schema="missing", native_args=native))
            )


def test_structured_retention_counts_both_representations_and_refunds() -> None:
    retain = Retention(StateBudget(ConsumerLimits(state_bytes=14, records=1)))
    first = retain_answer({"a": 1}, retain)
    assert first.output == '{"a":1}'
    values: list[JsonValue] = [None, False, 0, "", [], {}]
    for value in values:
        answer = retain_answer(value, retain)
        assert answer.value == value
        assert answer.output == encode_json(value)
    with pytest.raises(ConsumerFailure, match="retained output state"):
        retain_answer({"a": 12}, retain)
    assert retain_answer({"a": 1}, retain).value == {"a": 1}


@pytest.mark.parametrize(
    "value", [float("nan"), float("inf"), "\ud800", {"\ud800": 1}, {1: True}, (1,), 10**129]
)
def test_structured_output_rejects_non_json_values(value: object) -> None:
    with pytest.raises(ConsumerFailure):
        retain_answer(value, Retention(StateBudget(ConsumerLimits())))


def test_deep_output_is_guarded_before_copy_and_serialization() -> None:
    value: JsonValue = 0
    for _ in range(JSON_DEPTH + 1):
        value = [value]
    with pytest.raises(ConsumerFailure, match="nesting"):
        retain_answer(value, Retention(StateBudget(ConsumerLimits())))
    resolved = ResolvedProfile(BY_NAME["claude"], None, ("fake",), Options())
    result = normalize(
        resolved,
        ProcessResult(RawCapture(), b"", 0, 0),
        DecodedOutput(structured_output=value, structured_output_present=True),
    )
    with pytest.raises(ValueError, match="nesting"):
        result_dict(result)
    cyclic: list[object] = []
    cyclic.append(cyclic)
    with pytest.raises(ConsumerFailure, match="nesting"):
        retain_answer(cyclic, Retention(StateBudget(ConsumerLimits())))


@pytest.mark.parametrize("consumed", [False, True])
@pytest.mark.parametrize("native_exit", [0, 9, -signal.SIGTERM])
def test_schema_failure_cleanup_preserves_native_precedence(
    consumed: bool, native_exit: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    groups: list[int] = []
    monkeypatch.setattr(dispatch, "cleanup_process_group", groups.append)
    resolved = ResolvedProfile(BY_NAME["claude"], None, ("fake",), Options(schema="source"))
    decoded = DecodedOutput(error=ResultError("protocol_error", "missing structured answer"))
    capture = ConsumedCapture(decoded) if consumed else RawCapture(b"{}")
    process = ProcessResult(capture, b"", native_exit, 0, process_group=123)
    process, result, _ = dispatch._decode_process(resolved, process)
    assert groups == [123]
    error = normalize(resolved, process, result).error
    assert error is not None
    assert error.code == (
        "native_signal" if native_exit < 0 else "native_exit" if native_exit else "protocol_error"
    )


def test_schema_cleanup_failure_is_observable_with_native_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(dispatch, "cleanup_process_group", lambda group: "test cleanup denied")
    resolved = ResolvedProfile(BY_NAME["claude"], None, ("fake",), Options(schema="source"))
    process = ProcessResult(RawCapture(b"{}"), b"", 7, 0, process_group=123)
    process, decoded, stderr = dispatch._decode_process(resolved, process)
    assert "test cleanup denied" in stderr
    assert normalize(resolved, process, decoded).exit_code == 7


def test_schema_inventory(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["agents", "--json"]) == 0
    records = json.loads(capsys.readouterr().out)["agents"]
    assert {item["name"] for item in records if item["capabilities"]["schema"]} == {
        "claude",
        "codex",
        "qwen",
    }


def test_parse_json_preserves_boundary_depth_and_escaped_brackets() -> None:
    text = "[" * JSON_DEPTH + '"[\\"{"' + "]" * JSON_DEPTH
    assert encode_json(parse_json(text)) == text


def _fake_claude(tmp_path: Path, body: str) -> Path:
    script = tmp_path / "native.py"
    script.write_text(body)
    config = tmp_path / ".pratfile"
    config.write_text(
        "version=1\n[agents.claude]\ncommand=" + json.dumps([sys.executable, str(script)]) + "\n"
    )
    schema = tmp_path / "schema.json"
    schema.write_text('{"type":"object","properties":{"answer":{"type":"integer"}}}')
    return schema


def _native_success() -> dict[str, object]:
    return {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "structured_output": {"answer": 42},
        "usage": {"input_tokens": 2, "output_tokens": 3},
        "modelUsage": {"native-model": {}},
        "total_cost_usd": 0.25,
    }


@pytest.mark.parametrize("json_mode", [False, True])
def test_schema_fake_run_exact_argv_answer_trace_and_accounting(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], json_mode: bool
) -> None:
    value = _native_success()
    text = json.dumps(value)
    schema = _fake_claude(
        tmp_path,
        "import json, sys\nfrom pathlib import Path\n"
        'Path("call.json").write_text(json.dumps({"argv": sys.argv[1:], "stdin": sys.stdin.read()}))\n'
        f"print({text!r})\n",
    )
    args = ["cc", "--schema", str(schema), "--trace", "task"] + (["--json"] if json_mode else [])
    assert main(args) == 0
    streams = capsys.readouterr()
    if json_mode:
        result = json.loads(streams.out)
        assert result["schema_version"] == 1
        assert result["output"] == '{"answer":42}'
        assert result["structured_output"] == {"answer": 42}
        assert "structured_output_present" not in result
        assert result["usage"]["input_tokens"] == 2
        assert result["reported_models"] == ["native-model"]
        assert result["cost_usd"] == 0.25
    else:
        assert streams.out == '{"answer":42}\n'
    assert text in streams.err
    assert json.loads((tmp_path / "call.json").read_text()) == {
        "argv": ["-p", "--output-format", "json", "--json-schema", schema.read_text()],
        "stdin": "task",
    }


@pytest.mark.parametrize("exit_code", [0, 7])
def test_schema_fake_missing_answer_preserves_native_exit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], exit_code: int
) -> None:
    value = _native_success()
    value.pop("structured_output")
    value["result"] = "partial"
    schema = _fake_claude(
        tmp_path, f"import sys\nprint({json.dumps(value)!r})\nsys.exit({exit_code})\n"
    )
    assert main(["cc", "--schema", str(schema), "--json", "task"]) == (exit_code or 1)
    result = json.loads(capsys.readouterr().out)
    assert result["output"] == "partial"
    assert result["error"]["code"] == ("native_exit" if exit_code else "protocol_error")
    assert result["usage"]["input_tokens"] == 2


def test_schema_fake_timeout_keeps_structured_answer(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    schema = _fake_claude(
        tmp_path,
        f"import time\nprint({json.dumps(_native_success())!r}, flush=True)\ntime.sleep(20)\n",
    )
    assert main(["cc", "--schema", str(schema), "--timeout", "1", "--json", "task"]) == 124
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "timeout"
    assert result["structured_output"] == {"answer": 42}
    assert result["output"] == '{"answer":42}'


@pytest.mark.parametrize("timeout, interruption", [(True, None), (False, signal.SIGINT)])
def test_schema_protocol_failure_never_overrides_timeout_or_interruption(
    timeout: bool, interruption: int | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dispatch, "cleanup_process_group", lambda group: None)
    resolved = ResolvedProfile(BY_NAME["claude"], None, ("fake",), Options(schema="source"))
    process = ProcessResult(
        RawCapture(b"{}"),
        b"",
        -15,
        0,
        timed_out=timeout,
        interrupted_by=interruption,
        process_group=123,
    )
    process, decoded, _ = dispatch._decode_process(resolved, process)
    result = normalize(resolved, process, decoded)
    assert result.status == ("timeout" if timeout else "interrupted")
    assert result.exit_code == (124 if timeout else 130)


def test_schema_failure_cleans_descendant_after_parent_exit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    schema = _fake_claude(
        tmp_path,
        "import os, signal, sys, time\nfrom pathlib import Path\n"
        "child = os.fork()\n"
        "if child == 0:\n"
        "    os.close(0); os.close(1); os.close(2)\n"
        "    def stop(signum, frame):\n"
        '        Path("terminated").write_text("yes")\n'
        "        os._exit(0)\n"
        "    signal.signal(signal.SIGTERM, stop)\n"
        '    Path("ready").write_text("yes")\n'
        "    while True: time.sleep(0.1)\n"
        'while not Path("ready").exists(): time.sleep(0.01)\n'
        'print("{}", flush=True)\n',
    )
    assert main(["cc", "--schema", str(schema), "--json", "task"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["error"]["code"] == "protocol_error"
    assert (tmp_path / "terminated").read_text() == "yes"


@pytest.mark.parametrize("failure", ["directory", "file"])
def test_snapshot_setup_failure_keeps_origin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    source = tmp_path / "source"
    source.write_text("{}")

    def denied(*args: object, **kwargs: object) -> int:
        raise OSError("snapshot storage unavailable")

    if failure == "directory":
        monkeypatch.setattr(schema_module, "TemporaryDirectory", denied)
    else:
        original = os.open

        def open_snapshot(
            path: str | Path, flags: int, mode: int = 0o777, *, dir_fd: int | None = None
        ) -> int:
            if str(path).endswith("schema.json"):
                return denied()
            return original(path, flags, mode, dir_fd=dir_fd)

        monkeypatch.setattr(os, "open", open_snapshot)
    with (
        pytest.raises(PratError, match="snapshot storage unavailable") as caught,
        prepare_schema(str(source), ORIGIN, "file"),
    ):
        pytest.fail("snapshot failure accepted")
    assert caught.value.code == "invalid_config"
    assert str(caught.value).startswith(ORIGIN.label)


def test_schema_inherited_unsupported_profile_fails_before_reading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / ".pratfile").write_text(
        'version=1\n[defaults]\nschema="missing"\n[profiles.unused]\nagent="gemini"\n'
    )

    def forbidden(*args: object, **kwargs: object) -> bytes:
        pytest.fail("schema or prompt acquired before capability check")

    monkeypatch.setattr(dispatch, "prepare_schema", forbidden)
    monkeypatch.setattr(dispatch, "acquire_prompt", forbidden)
    assert main(["claude", "--schema", "override", "--json", "task"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["error"]["code"] == "invalid_config"
    assert "defaults.schema" in result["error"]["message"]


@pytest.mark.parametrize("phase", ["prompt", "execute", "decode", "preview", "success"])
def test_snapshot_context_covers_dispatch_exits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], phase: str
) -> None:
    schema = _fake_claude(tmp_path, f"print({json.dumps(_native_success())!r})\n")
    snapshots: list[Path] = []
    original_build = claude.build

    @contextmanager
    def prepare(
        source: str, origin: OptionOrigin, transport: Literal["inline", "file"]
    ) -> Iterator[PreparedSchema]:
        with prepare_schema(source, origin, transport) as prepared:
            assert prepared.path is not None
            snapshots.append(Path(prepared.path))
            yield prepared

    monkeypatch.setattr(dispatch, "prepare_schema", prepare)

    def build(resolved: ResolvedProfile, prompt: bytes) -> Invocation:
        assert resolved.prepared_schema is not None
        assert resolved.prepared_schema.path is not None
        path = Path(resolved.prepared_schema.path)
        assert path.read_text() == schema.read_text()
        invocation = original_build(resolved, prompt)
        return replace(invocation, argv=(*invocation.argv[:-1], str(path)))

    def decode(stdout: str) -> DecodedOutput:
        assert snapshots[-1].read_text() == schema.read_text()
        if phase == "decode":
            raise OSError("decoding failed")
        return claude.decode_schema(stdout)

    adapter = replace(
        ADAPTERS["claude"], schema_transport="file", build=build, schema_whole_document=decode
    )
    monkeypatch.setattr(dispatch, "ADAPTERS", {**ADAPTERS, "claude": adapter})
    if phase == "prompt":

        def interrupted(*args: object, **kwargs: object) -> bytes:
            raise InputInterrupted(signal.SIGINT)

        monkeypatch.setattr(dispatch, "acquire_prompt", interrupted)
    elif phase == "execute":

        def broken(*args: object, **kwargs: object) -> None:
            raise OSError("execution failed")

        monkeypatch.setattr(dispatch, "_execute", broken)
    code = main(
        ["cc", "--schema", str(schema), "--json", "task"]
        + (["--dry-run"] if phase == "preview" else [])
    )
    result = json.loads(capsys.readouterr().out)
    assert code == (130 if phase == "prompt" else 1 if phase in {"execute", "decode"} else 0)
    if phase == "success":
        assert result["structured_output"] == {"answer": 42}
    if phase == "preview":
        assert result["schema"] == str(schema)
        assert result["schema_transport"] == "temporary prepared file"
        assert result["argv"][-1] == "<temporary prepared schema>"
    for path in snapshots:
        assert not path.parent.exists()


@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM])
def test_schema_fake_interruption_keeps_final_answer(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], signum: int
) -> None:
    schema = _fake_claude(
        tmp_path,
        "import time\nfrom pathlib import Path\n"
        f"print({json.dumps(_native_success())!r}, flush=True)\n"
        'Path("ready").touch()\ntime.sleep(30)\n',
    )
    delivered: list[int] = []
    stop = threading.Event()

    def interrupt() -> None:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not stop.is_set():
            if (tmp_path / "ready").exists():
                delivered.append(signum)
                os.kill(os.getpid(), signum)
                return
            stop.wait(0.01)

    sender = threading.Thread(target=interrupt)
    sender.start()
    try:
        code = main(["cc", "--schema", str(schema), "--timeout", "15", "--json", "task"])
    finally:
        stop.set()
        sender.join()
    assert delivered == [signum]
    assert code == 128 + signum
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "interrupted"
    assert result["structured_output"] == {"answer": 42}
    assert result["cost_usd"] == 0.25


@pytest.mark.parametrize("agent", ["codex", "qwen"])
@pytest.mark.parametrize("json_mode", [False, True])
@pytest.mark.parametrize("exit_code", [0, 7])
@pytest.mark.parametrize("malformed", [False, True])
def test_schema_file_native_transport_and_cleanup(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    agent: str,
    json_mode: bool,
    exit_code: int,
    malformed: bool,
) -> None:
    source = tmp_path / "schema source.json"
    original = b'{ "type": "object", "title": "original" }\n'
    source.write_bytes(original)
    snapshot_log = tmp_path / "call.json"
    script = tmp_path / "native.py"
    flag = "--output-schema" if agent == "codex" else "--json-schema"
    native = (
        '{"type":"result","subtype":"success","is_error":false,"structured_result":'
        '{"answer":42},"usage":{"input_tokens":2,"output_tokens":3}}\n'
    )
    if agent == "codex":
        native = (
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"id": "a", "type": "agent_message", "text": '{"answer":42}'},
                }
            )
            + "\n"
            + json.dumps(
                {
                    "type": "turn.completed",
                    "usage": {"input_tokens": 2, "cached_input_tokens": 0, "output_tokens": 3},
                }
            )
            + "\n"
        )
    if malformed:
        native += (
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"id": "later", "type": "agent_message", "text": "malformed final"},
                }
            )
            + '\n{"type":"turn.completed","usage":{"input_tokens":2,'
            '"cached_input_tokens":0,"output_tokens":3}}\n'
            if agent == "codex"
            else '{"type":"result","subtype":"success","is_error":false,'
            '"result":"missing structured_result","usage":{"input_tokens":2,"output_tokens":3}}\n'
        )
    script.write_text(
        "import json,sys\nfrom pathlib import Path\n"
        f"arg=sys.argv[sys.argv.index({flag!r})+1]\n"
        "path=Path(arg.removeprefix('@'))\n"
        f"Path({str(source)!r}).write_text('changed source')\n"
        f"Path({str(snapshot_log)!r}).write_text(json.dumps({{"
        "'argv':sys.argv[1:],'stdin':sys.stdin.read(),'snapshot':str(path),"
        "'data':path.read_text(),'mode':path.stat().st_mode & 0o777,"
        "'directory_mode':path.parent.stat().st_mode & 0o777}))\n"
        f"print({native!r},end='')\nsys.exit({exit_code})\n"
    )
    config = tmp_path / ".pratfile"
    config.write_text(
        f"version=1\n[agents.{agent}]\ncommand=" + json.dumps([sys.executable, str(script)]) + "\n"
    )
    args = [agent, "--schema", str(source), "--trace", "task"] + (["--json"] if json_mode else [])
    assert main(args) == (exit_code or int(malformed))
    captured = capsys.readouterr()
    assert native in captured.err
    if json_mode:
        result = json.loads(captured.out)
        assert result["output"] == '{"answer":42}'
        assert result["structured_output"] == {"answer": 42}
        assert result["usage"]["input_tokens"] == 2
        assert result["native_exit_code"] == exit_code
        assert result["status"] == ("error" if exit_code or malformed else "success")
        if exit_code or malformed:
            assert result["error"]["code"] == ("native_exit" if exit_code else "protocol_error")
    else:
        assert captured.out == '{"answer":42}\n'
    call = json.loads(snapshot_log.read_text())
    snapshot = call.pop("snapshot")
    expected_argv = (
        ["exec", "--json", flag, snapshot, "-"]
        if agent == "codex"
        else ["--output-format", "stream-json", flag, "@" + snapshot]
    )
    assert call == {
        "argv": expected_argv,
        "stdin": "task",
        "data": original.decode(),
        "mode": 0o600,
        "directory_mode": 0o700,
    }
    assert not Path(snapshot).parent.exists()


@pytest.mark.parametrize("agent", ["codex", "qwen"])
def test_schema_file_preview_config_override_and_independent_cwd(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], agent: str
) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    source = config_dir / "profile.json"
    source.write_text('{"type":"object"}')
    chosen = tmp_path / "chosen.json"
    chosen.write_text('{"type":"object","title":"chosen"}')
    config = config_dir / "profiles.toml"
    config.write_text(f'version=1\n[profiles.work]\nagent="{agent}"\nschema="profile.json"\n')
    base = ["--config", str(config), "work", "--cwd", str(run_dir), "--dry-run", "--json", "task"]
    for extra, expected in (([], source), (["--schema", "chosen.json"], chosen)):
        assert main([*base, *extra]) == 0
        result = json.loads(capsys.readouterr().out)
        assert result["schema"] == str(expected)
        assert result["schema_transport"] == "temporary prepared file"
        assert result["cwd"] == str(run_dir)
        token = ("" if agent == "codex" else "@") + "<temporary prepared schema>"
        assert token in result["argv"]


@pytest.mark.parametrize("from_config", [False, True])
def test_qwen_schema_root_rejected_before_input_with_origin(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    from_config: bool,
) -> None:
    source = tmp_path / "schema.json"
    source.write_text('{"type":"array"}')

    def forbidden(*args: object, **kwargs: object) -> bytes:
        pytest.fail("prompt acquired before root validation")

    monkeypatch.setattr(dispatch, "acquire_prompt", forbidden)
    if from_config:
        (tmp_path / ".pratfile").write_text(
            'version=1\n[profiles.work]\nagent="qwen"\nschema="schema.json"\n'
        )
        args = ["work"]
    else:
        args = ["qwen", "--schema", str(source)]
    assert main([*args, "--json"]) == 2
    error = json.loads(capsys.readouterr().out)["error"]
    assert error["code"] == ("invalid_config" if from_config else "invalid_arguments")
    assert "root must accept objects" in error["message"]
    assert (
        "profiles.work.schema" if from_config else "command line: selector 'qwen'.schema"
    ) in error["message"]


@pytest.mark.parametrize("agent", ["codex", "qwen"])
@pytest.mark.parametrize("depth", [64, 65])
def test_schema_answer_depth_independent_of_event_envelope(agent: str, depth: int) -> None:
    answer = "[" * depth + "0" + "]" * depth
    if agent == "codex":
        text = (
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "id": "answer",
                        "type": "agent_message",
                        "text": answer,
                    },
                }
            )
            + '\n{"type":"turn.completed","usage":{"input_tokens":0,'
            '"cached_input_tokens":0,"output_tokens":0}}'
        )
    else:
        text = (
            '{"type":"result","subtype":"success","is_error":false,"structured_result":'
            + answer
            + "}"
        )
    decoded = ADAPTERS[agent].for_schema(True).decode(text)
    if depth == 64:
        assert decoded.error is None and decoded.output == answer
    else:
        assert decoded.error is not None and decoded.error.code == "protocol_error"


@pytest.mark.parametrize("agent", ["codex", "qwen"])
def test_schema_consumer_custom_numeric_budget(agent: str) -> None:
    factory = codex.schema_consumer if agent == "codex" else qwen.schema_consumer
    consumer = factory(ConsumerLimits(numeric_bytes=2))
    if agent == "codex":
        text = (
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "id": "answer",
                        "type": "agent_message",
                        "text": "123",
                    },
                }
            )
            + '\n{"type":"turn.completed","usage":{"input_tokens":0,'
            '"cached_input_tokens":0,"output_tokens":0}}'
        )
    else:
        text = '{"type":"result","subtype":"success","is_error":false,"structured_result":123}'
    consumer.feed(text.encode())
    decoded = consumer.finish()
    assert decoded.error is not None and decoded.error.code == "protocol_error"
    assert not decoded.structured_output_present
