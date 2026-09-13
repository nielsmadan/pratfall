from adapter_helpers import resolved
from pratfall.adapters import hermes
from pratfall.models import Options


def test_hermes_builds_quiet_stdin_invocation_without_yolo() -> None:
    invocation = hermes.build(
        resolved(
            "hermes",
            Options(
                model="provider/model",
                effort="ultra",
                max_turns=7,
                native_args=("--provider", "provider", "--checkpoints"),
            ),
        ),
        b"-leading prompt",
    )
    assert invocation.argv == (
        "hermes-wrapper",
        "native",
        "chat",
        "--oneshot",
        "--quiet",
        "--query-file",
        "-",
        "--model",
        "provider/model",
        "--reasoning",
        "ultra",
        "--max-turns",
        "7",
        "--provider",
        "provider",
        "--checkpoints",
    )
    assert invocation.stdin == b"-leading prompt"
    assert "-z" not in invocation.argv
