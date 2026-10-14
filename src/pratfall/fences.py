import re

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
