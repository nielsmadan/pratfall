from string import Template

import pytest

from pratfall.errors import PratError
from pratfall.prompt_input import PROMPT_LIMIT
from pratfall.prompt_templates import render_template, validate_template


@pytest.mark.parametrize(
    ("prompt", "base", "expected"),
    [
        ("Review $input", b"code", b"Review code"),
        ("${input}: $input", "雪".encode(), "雪: 雪".encode()),
        ("$$input: ${input}", b"$input ${input} $$", b"$input: $input ${input} $$"),
        ("Review", b"code\n", b"Review\n\ncode\n"),
        ("Review", b"", b"Review"),
        ("Review $input", b"", b"Review "),
        ("Cost $$5", b"input", b"Cost $5\n\ninput"),
    ],
)
def test_template_rendering_preserves_exact_bytes(
    prompt: str, base: bytes, expected: bytes
) -> None:
    validate_template(prompt, "template")
    assert render_template(prompt, base) == expected


@pytest.mark.parametrize("prompt", ["$", "$other", "${other}", "${input", "$1", "${}", "$Input"])
def test_invalid_placeholders_fail_validation(prompt: str) -> None:
    with pytest.raises(PratError, match=r"only.*placeholders") as caught:
        validate_template(prompt, "source: templates.work.prompt")
    assert caught.value.code == "invalid_config"
    assert str(caught.value).startswith("source: templates.work.prompt:")


@pytest.mark.parametrize("prompt", ["$input", " \n${input}\n"])
def test_template_must_produce_a_task(prompt: str) -> None:
    with pytest.raises(PratError, match="empty or whitespace"):
        render_template(prompt, b"")


@pytest.mark.parametrize("prompt", ["x" * (PROMPT_LIMIT + 1), "é" * (PROMPT_LIMIT // 2 + 1)])
def test_template_definition_has_utf8_byte_limit(prompt: str) -> None:
    with pytest.raises(PratError, match="byte limit"):
        validate_template(prompt, "template")


@pytest.mark.parametrize("prompt", ["$input$input", "Task"])
@pytest.mark.parametrize("excess", [0, 1])
def test_expansion_limit_is_checked_before_substitution(
    prompt: str, excess: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = b"x" * (
        PROMPT_LIMIT // 2 + excess if prompt == "$input$input" else PROMPT_LIMIT - 6 + excess
    )
    if excess:

        def unexpected_substitution(_self: Template, **_kwargs: str) -> str:
            pytest.fail("oversized expansion must fail before substitution")

        monkeypatch.setattr(Template, "substitute", unexpected_substitution)
        with pytest.raises(PratError, match="byte limit"):
            render_template(prompt, base)
    else:
        expected = base + base if prompt == "$input$input" else b"Task\n\n" + base
        assert render_template(prompt, base) == expected


def test_editor_can_receive_blank_rendered_template() -> None:
    assert render_template("$input", b" \n", allow_blank=True) == b" \n"
