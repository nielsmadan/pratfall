import json

from pratfall.catalog import BY_NAME
from pratfall.models import Options, ResolvedProfile


def resolved(agent: str, options: Options | None = None) -> ResolvedProfile:
    return ResolvedProfile(
        BY_NAME[agent], "test", (f"{agent}-wrapper", "native"), options or Options()
    )


def codex_stream(*events: object) -> str:
    return "\n".join(json.dumps(event, ensure_ascii=False) for event in events) + "\n"


def codex_usage(**changes: object) -> dict[str, object]:
    usage: dict[str, object] = {
        "input_tokens": 24_901,
        "cached_input_tokens": 8_960,
        "cache_write_input_tokens": 0,
        "output_tokens": 8,
        "reasoning_output_tokens": 0,
    }
    usage.update(changes)
    return usage


def antigravity_usage(**changes: object) -> dict[str, object]:
    usage: dict[str, object] = {
        "input_tokens": 10,
        "output_tokens": 4,
        "thinking_tokens": 2,
        "cache_read_tokens": 3,
        "total_tokens": 14,
    }
    usage.update(changes)
    return usage


def copilot_result(exit_code: object = 0) -> dict[str, object]:
    return {
        "type": "result",
        "timestamp": "2026-09-09T12:00:00.000Z",
        "sessionId": "session",
        "exitCode": exit_code,
        "usage": {
            "premiumRequests": 1,
            "totalApiDurationMs": 100,
            "sessionDurationMs": 120,
            "codeChanges": {"linesAdded": 2, "linesRemoved": 1, "filesModified": 1},
        },
    }


def opencode_usage(**changes: object) -> dict[str, object]:
    usage: dict[str, object] = {
        "total": 18,
        "input": 10,
        "output": 4,
        "reasoning": 2,
        "cache": {"read": 3, "write": 1},
    }
    usage.update(changes)
    return usage
