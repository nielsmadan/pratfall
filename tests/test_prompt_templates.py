from string import Template

import pytest

from pratfall.errors import PratError
from pratfall.prompt_input import PROMPT_LIMIT, PromptSource
from pratfall.prompt_templates import (
    BoundInput,
    bind_input,
    bind_run_input,
    render_template,
    template_parameters,
    validate_template,
)


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


@pytest.mark.parametrize("prompt", ["$", "Cost $", "${input", "$1", "${}", "${a b}", "$-x"])
def test_invalid_placeholder_syntax_fails_validation(prompt: str) -> None:
    with pytest.raises(PratError) as caught:
        validate_template(prompt, "source: templates.work.prompt")
    assert caught.value.code == "invalid_config"
    assert str(caught.value) == (
        "source: templates.work.prompt: use $name or ${name} placeholders "
        "and $$ for a literal dollar."
    )


@pytest.mark.parametrize(("prompt", "identifier"), [("$Input", "Input"), ("${INPUT}", "INPUT")])
def test_input_case_near_miss_fails_validation(prompt: str, identifier: str) -> None:
    with pytest.raises(PratError) as caught:
        validate_template(prompt, "templates.work.prompt")
    assert caught.value.code == "invalid_config"
    assert str(caught.value) == (
        f"templates.work.prompt: placeholder '{identifier}' differs from 'input' only in case; "
        "use $input or ${input} for the base prompt."
    )


@pytest.mark.parametrize("prompt", ["Clean $folder", "${_x9} $other $input $$"])
def test_named_parameters_pass_validation(prompt: str) -> None:
    validate_template(prompt, "template")


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("Move $src to ${dst}, then $src again", ("src", "dst")),
        ("${x} and $x", ("x",)),
        ("$input $b $a ${input} $b", ("b", "a")),
        ("Cost $$amount and $$", ()),
        ("Plain", ()),
        ("$Name $name", ("Name", "name")),
    ],
)
def test_template_parameters_are_unique_in_first_appearance_order(
    prompt: str, expected: tuple[str, ...]
) -> None:
    assert template_parameters(prompt) == expected


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


LABEL = "profile 'clean' prompt"
TWO = "Move $src to $dst"


def bind(
    words: tuple[str, ...] = (),
    named: tuple[str, ...] = (),
    prompt: str | None = TWO,
    explicit: PromptSource | None = None,
) -> BoundInput:
    return bind_run_input(prompt, LABEL, words, named, explicit)


def binding_error(
    words: tuple[str, ...] = (), named: tuple[str, ...] = (), prompt: str | None = TWO
) -> str:
    with pytest.raises(PratError) as caught:
        bind(words, named, prompt)
    assert caught.value.code == "invalid_arguments"
    assert caught.value.exit_code == 2
    return str(caught.value)


@pytest.mark.parametrize(
    ("words", "named"),
    [
        (("a", "b"), ()),
        ((), ("src=a", "dst=b")),
        ((), ("dst=b", "src=a")),
        (("a",), ("dst=b",)),
    ],
)
def test_parameters_bind_positionally_and_by_name(
    words: tuple[str, ...], named: tuple[str, ...]
) -> None:
    file_source = PromptSource("file", "notes.md")
    bound = bind_run_input(TWO, LABEL, words, named, file_source)
    assert bound.source == file_source
    assert dict(bound.parameters) == {"src": "a", "dst": "b"}
    assert list(bound.parameters) == ["src", "dst"]


def test_named_parameter_splits_at_first_equals_and_keeps_dash_values() -> None:
    bound = bind((), ("src=a=b", "dst=-"))
    assert dict(bound.parameters) == {"src": "a=b", "dst": "-"}
    assert bound.source is None


def test_too_many_positionals_suggest_free_text_options() -> None:
    assert binding_error(("a", "b", "c")) == (
        "profile 'clean' prompt takes 2 parameter value(s) (src, dst) but got 3; "
        "pass free text with --prompt=TEXT or --file PATH."
    )


def test_lone_dash_positional_is_rejected_in_parameter_mode() -> None:
    assert binding_error(("a", "-")) == (
        "profile 'clean' prompt: '-' cannot be the value of parameter 'dst'; "
        "use --param dst=- for a literal dash or --file - to read standard input."
    )


@pytest.mark.parametrize(
    ("named", "message"),
    [
        (("src",), "profile 'clean' prompt: --param 'src' must have the form NAME=VALUE."),
        (("=a",), "profile 'clean' prompt: --param '=a' has an empty parameter name."),
        (
            ("other=a",),
            "profile 'clean' prompt has no parameter 'other'; its parameters are src, dst.",
        ),
        (
            ("input=a",),
            (
                "profile 'clean' prompt: 'input' is not a parameter; provide the base prompt "
                "with --prompt=TEXT, --file PATH, or standard input."
            ),
        ),
    ],
)
def test_malformed_named_parameters_are_rejected(named: tuple[str, ...], message: str) -> None:
    assert binding_error((), named) == message


@pytest.mark.parametrize(
    ("words", "named"),
    [(("a",), ("src=b", "dst=c")), ((), ("src=a", "src=b", "dst=c"))],
)
def test_parameter_bound_twice_is_rejected(words: tuple[str, ...], named: tuple[str, ...]) -> None:
    assert binding_error(words, named) == (
        "profile 'clean' prompt: parameter 'src' is bound more than once."
    )


@pytest.mark.parametrize(
    ("words", "named", "missing"),
    [((), (), "src, dst"), (("a",), (), "dst"), ((), ("dst=b",), "src")],
)
def test_unbound_parameters_are_all_listed(
    words: tuple[str, ...], named: tuple[str, ...], missing: str
) -> None:
    assert binding_error(words, named) == (
        f"profile 'clean' prompt: missing value for parameter(s) {missing}."
    )


@pytest.mark.parametrize(
    ("value", "problem"),
    [
        ("", "must not be empty or whitespace-only"),
        (" \n\t", "must not be empty or whitespace-only"),
        ("a\0b", "must not contain NUL bytes"),
        ("bad\udcff", "is not valid UTF-8"),
    ],
)
def test_invalid_parameter_values_are_rejected(value: str, problem: str) -> None:
    assert binding_error(("a",), (f"dst={value}",)) == (
        f"profile 'clean' prompt: parameter 'dst' {problem}."
    )
    assert binding_error((value, "b")) == f"profile 'clean' prompt: parameter 'src' {problem}."


@pytest.mark.parametrize(
    ("prompt", "message"),
    [
        (None, "--param needs a prompt with parameters, but no template is active."),
        (
            "Review $input",
            "--param needs a prompt with parameters, but profile 'clean' prompt has no parameters.",
        ),
    ],
)
def test_named_parameters_need_a_parameterized_template(prompt: str | None, message: str) -> None:
    assert binding_error((), ("src=a",), prompt) == message


@pytest.mark.parametrize("prompt", [None, "Review $input"])
@pytest.mark.parametrize(
    ("words", "explicit", "expected"),
    [
        ((), None, None),
        ((), PromptSource("file", "-"), PromptSource("file", "-")),
        (("fix it",), None, PromptSource("inline", "fix it")),
        (("-",), None, PromptSource("stdin")),
    ],
)
def test_words_become_one_prompt_source_without_parameters(
    prompt: str | None,
    words: tuple[str, ...],
    explicit: PromptSource | None,
    expected: PromptSource | None,
) -> None:
    bound = bind(words, (), prompt, explicit)
    assert bound.source == expected
    assert dict(bound.parameters) == {}


@pytest.mark.parametrize(
    ("words", "explicit"),
    [
        (("a", "b"), None),
        (("a",), PromptSource("inline", "b")),
        (("-",), PromptSource("file", "x")),
    ],
)
def test_more_than_one_prompt_source_is_rejected_without_parameters(
    words: tuple[str, ...], explicit: PromptSource | None
) -> None:
    with pytest.raises(PratError) as caught:
        bind(words, (), None, explicit)
    assert caught.value.code == "invalid_arguments"
    assert str(caught.value) == "Provide exactly one prompt source."


@pytest.mark.parametrize(
    ("prompt", "base", "parameters", "expected"),
    [
        ("Use $a and ${b}", b"", {"a": "$b", "b": "${a} $$"}, b"Use $b and ${a} $$"),
        ("$a, $a, ${a}", b"", {"a": "x"}, b"x, x, x"),
        ("$a: $input ($a)", b"base", {"a": "\u96ea"}, "\u96ea: base (\u96ea)".encode()),
        ("Clean $folder", b"also this", {"folder": "src"}, b"Clean src\n\nalso this"),
        ("Clean $folder", b"", {"folder": "src"}, b"Clean src"),
    ],
)
def test_parameters_render_literally(
    prompt: str, base: bytes, parameters: dict[str, str], expected: bytes
) -> None:
    validate_template(prompt, "template")
    assert render_template(prompt, base, parameters=parameters) == expected


def test_parameter_placeholder_without_value_is_a_programming_error() -> None:
    with pytest.raises(KeyError):
        render_template("Clean $folder", b"")


@pytest.mark.parametrize("excess", [0, 1])
def test_parameter_values_count_utf8_bytes_toward_limit(
    excess: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = "\u00e9" * (PROMPT_LIMIT // 4) + "x" * excess
    if excess:

        def unexpected_substitution(_self: Template, *_args: object, **_kwargs: str) -> str:
            pytest.fail("oversized expansion must fail before substitution")

        monkeypatch.setattr(Template, "substitute", unexpected_substitution)
        with pytest.raises(PratError, match="byte limit") as caught:
            render_template("$a$a", b"", parameters={"a": value})
        assert caught.value.code == "invalid_arguments"
    else:
        rendered = render_template("$a$a", b"", parameters={"a": value})
        assert len(rendered) == PROMPT_LIMIT


@pytest.mark.parametrize(
    ("active", "message"),
    [
        (False, "--param needs a prompt with parameters, but no template is active."),
        (True, "--param needs a prompt with parameters, but workflow 'tidy' has no parameters."),
    ],
)
def test_bind_input_rejects_named_values_without_parameters(active: bool, message: str) -> None:
    with pytest.raises(PratError) as caught:
        bind_input((), "workflow 'tidy'", (), ("src=a",), None, active=active)
    assert caught.value.code == "invalid_arguments"
    assert str(caught.value) == message


def test_bind_input_binds_the_given_names_without_a_prompt() -> None:
    bound = bind_input(
        ("folder", "level"), "workflow 'tidy'", ("src",), ("level=3",), None, active=True
    )
    assert bound.source is None
    assert list(bound.parameters.items()) == [("folder", "src"), ("level", "3")]


def test_bind_input_without_names_uses_one_word_as_the_prompt() -> None:
    bound = bind_input((), "workflow 'tidy'", ("fix it",), (), None, active=True)
    assert bound.source == PromptSource("inline", "fix it")
    assert dict(bound.parameters) == {}
