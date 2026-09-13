from adapter_helpers import resolved
from pratfall.adapters import kiro
from pratfall.models import Options


def test_kiro_builds_protected_text_invocation() -> None:
    invocation = kiro.build(
        resolved(
            "kiro",
            Options(
                model="claude-sonnet-4",
                effort="xhigh",
                native_args=("--agent", "reviewer", "--trust-tools=read,grep"),
            ),
        ),
        b"--leading prompt",
    )
    assert invocation.argv == (
        "kiro-wrapper",
        "native",
        "chat",
        "--no-interactive",
        "--wrap",
        "never",
        "--model",
        "claude-sonnet-4",
        "--effort",
        "xhigh",
        "--agent",
        "reviewer",
        "--trust-tools=read,grep",
        "--",
        "--leading prompt",
    )
    assert invocation.stdin == b""
