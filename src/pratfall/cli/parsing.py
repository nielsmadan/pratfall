import argparse
import math
import os
import textwrap
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, fields
from importlib.metadata import version
from pathlib import Path
from types import MappingProxyType
from typing import Any, NoReturn

from pratfall.catalog import MANAGEMENT_COMMANDS
from pratfall.config import validate_native_agent, validate_session_id
from pratfall.errors import PratError, did_you_mean
from pratfall.instructions import validate_instructions
from pratfall.models import MAX_TURNS, OptionOrigin, Options
from pratfall.prompt_input import PromptSource

_TRI_STATE_FLAGS: Mapping[str, tuple[str, bool]] = MappingProxyType(
    {
        "--fast": ("fast", True),
        "--no-fast": ("fast", False),
        "--notify": ("notify", True),
        "--no-notify": ("notify", False),
    }
)


_RUN_SWITCHES = frozenset(
    {"--json", "--dry-run", "--progress", "--trace", "-x", "--extract", "-e", "--edit"}
)
_RUN_FLAGS_WITHOUT_VALUE = _RUN_SWITCHES | frozenset(_TRI_STATE_FLAGS)

_MANAGEMENT_HELP: Mapping[str, str] = MappingProxyType(
    {
        "list": "List profiles, workflows, templates and agents.",
        "doctor": "Locate agent executables without running them or checking credentials.",
        "config": "Manage TOML configuration.",
        "trust": "Trust a directory, and everything under it, to load its .pratfile.",
        "completion": "Print bash or zsh completion code to eval from a shell rc file.",
        "help": "Show help for prat, a selector, or a management command.",
        "version": "Show the version and exit.",
    }
)

_CONFIG_COMMAND_HELP: Mapping[str, str] = MappingProxyType(
    {
        "path": "Print the global config path, or --config PATH.",
        "init": "Create an example config; fail if the path already exists.",
        "validate": (
            "Validate every configured profile, template and workflow without launching an agent."
        ),
    }
)


LIST_SECTIONS: Mapping[str, str] = MappingProxyType(
    {
        "profiles": "Configured profiles.",
        "workflows": "Configured workflows.",
        "templates": "Configured prompt templates.",
        "agents": "Built-in agents and their aliases.",
    }
)

SHELLS = ("bash", "zsh")

_COMMAND_EXAMPLES: Mapping[tuple[str, ...], tuple[str, ...]] = MappingProxyType(
    {
        ("list",): ("prat list", "prat list profiles --json"),
        ("doctor",): ("prat doctor --versions",),
        ("config",): ("prat config validate",),
        ("config", "path"): ("prat config path",),
        ("config", "init"): ("prat config init --config ./team.toml",),
        ("config", "validate"): ("prat config validate --config .pratfile",),
        ("trust",): ("prat trust ~/src/team-repo",),
        ("completion",): ('eval "$(prat completion zsh)"',),
        ("help",): ("prat help cc",),
        ("version",): ("prat version --json",),
    }
)


_RUN_VALUE_FLAGS: Mapping[str, str] = MappingProxyType(
    {
        "--config": "config",
        "--cwd": "cwd",
        "--effort": "effort",
        "--max-ai-credits": "max_ai_credits",
        "--max-budget-usd": "max_budget_usd",
        "--max-turns": "max_turns",
        "--model": "model",
        "--schema": "schema",
        "--native-agent": "native_agent",
        "--session-id": "session_id",
        "--timeout": "timeout",
        "--file": "file",
        "-f": "file",
        "--context": "context",
        "--add-dir": "add_dirs",
        "--attachment": "attachments",
        "--tools": "tools",
        "--disabled-tools": "disabled_tools",
        "--instructions": "instructions",
        "--instructions-file": "instructions_file",
        "--template": "template",
        "-t": "template",
        "--param": "parameters",
        "--prompt": "prompt",
    }
)

_OPTION_FLAGS: Mapping[str, str] = MappingProxyType(
    {
        **{field: flag for flag, field in _RUN_VALUE_FLAGS.items() if flag.startswith("--")},
        "native_args": "arguments after --",
    }
)


def option_flags(options: Options) -> dict[str, str]:
    tri_state = {
        field: flag
        for flag, (field, value) in _TRI_STATE_FLAGS.items()
        if getattr(options, field) is value
    }
    return {**_OPTION_FLAGS, **tri_state}


SELECTOR_REQUIRED = "a selector is required; run 'prat --help'"

_RUN_FLAG_NAMES = frozenset({*_RUN_VALUE_FLAGS, *_RUN_FLAGS_WITHOUT_VALUE})

_WORKFLOW_FLAGS = frozenset(
    {
        "--param",
        "--file",
        "-f",
        "--prompt",
        "--config",
        "--cwd",
        "--timeout",
        "--json",
        "--dry-run",
        "--progress",
        "--trace",
        "--notify",
        "--no-notify",
    }
)


@dataclass(frozen=True)
class _RunOption:
    label: str
    flags: tuple[str, ...]
    help: str


_RUN_OPTIONS: tuple[_RunOption, ...] = (
    _RunOption("--prompt TEXT", ("--prompt",), "Pass the prompt as text."),
    _RunOption(
        "-f, --file PATH", ("-f", "--file"), "Read the prompt from a UTF-8 file; use - for stdin."
    ),
    _RunOption(
        "-t, --template NAME",
        ("-t", "--template"),
        "Apply a named prompt template; additional input is optional.",
    ),
    _RunOption("--param NAME=VALUE", ("--param",), "Fill a prompt parameter; repeat for several."),
    _RunOption(
        "--context PATH",
        ("--context",),
        "Prepend a UTF-8 context file, or - for stdin; repeat to include several.",
    ),
    _RunOption(
        "-x, --extract", ("-x", "--extract"), "Return the first fenced code block from the answer."
    ),
    _RunOption(
        "-e, --edit", ("-e", "--edit"), "Edit the complete prompt in VISUAL, EDITOR, or vi."
    ),
    _RunOption("--model MODEL", ("--model",), "Override the profile model."),
    _RunOption(
        "--native-agent NAME",
        ("--native-agent",),
        "Select a native agent/persona for Claude, Copilot, or Vibe.",
    ),
    _RunOption(
        "--session-id ID",
        ("--session-id",),
        "Set the native session id so its transcript can be located. A value matching an "
        "existing Copilot session resumes it.",
    ),
    _RunOption("--effort EFFORT", ("--effort",), "Override the native effort setting."),
    _RunOption(
        "--fast / --no-fast",
        ("--fast", "--no-fast"),
        "Enable or disable supported native fast mode for this run.",
    ),
    _RunOption("--timeout SECONDS", ("--timeout",), "Set the wall-clock deadline."),
    _RunOption("--max-budget-usd USD", ("--max-budget-usd",), "Set a supported native USD budget."),
    _RunOption("--max-turns COUNT", ("--max-turns",), "Set a supported native turn limit."),
    _RunOption(
        "--max-ai-credits COUNT",
        ("--max-ai-credits",),
        "Set Copilot's soft per-response AI-credit limit.",
    ),
    _RunOption("--cwd PATH", ("--cwd",), "Set the agent working directory."),
    _RunOption(
        "--add-dir PATH",
        ("--add-dir",),
        "Add a native workspace directory; repeat to include several.",
    ),
    _RunOption(
        "--attachment PATH",
        ("--attachment",),
        "Attach a native image/file; repeat where supported.",
    ),
    _RunOption(
        "--tools NAME", ("--tools",), "Restrict native tool availability; repeat for several."
    ),
    _RunOption(
        "--disabled-tools NAME",
        ("--disabled-tools",),
        "Disable native tools or patterns; repeat for several.",
    ),
    _RunOption(
        "--instructions TEXT",
        ("--instructions",),
        "Append instructions while preserving native built-in guidance.",
    ),
    _RunOption(
        "--instructions-file PATH",
        ("--instructions-file",),
        "Read appended instructions from a UTF-8 file.",
    ),
    _RunOption(
        "--config PATH", ("--config",), "Merge PATH over global config instead of .pratfile."
    ),
    _RunOption(
        "--schema PATH", ("--schema",), "Request native JSON Schema output from a UTF-8 file."
    ),
    _RunOption("--json", ("--json",), "Print one normalized JSON result."),
    _RunOption("--progress", ("--progress",), "Print bounded live activity updates on stderr."),
    _RunOption("--trace", ("--trace",), "Print captured native stdout on stderr."),
    _RunOption(
        "--notify / --no-notify",
        ("--notify", "--no-notify"),
        "Show or suppress the completion notification for this run.",
    ),
    _RunOption(
        "--dry-run", ("--dry-run",), "Resolve and print the invocation without launching it."
    ),
)

_HELP_WIDTH = 100
_RUN_USAGE = "prat [RUN_OPTIONS] {selector} [PROMPT | VALUE...] [-- NATIVE_ARGS]"
_WORKFLOW_USAGE = "prat [RUN_OPTIONS] {selector} [PROMPT | VALUE...]"


def run_flag_help() -> dict[str, str]:
    return {flag: option.help for option in _RUN_OPTIONS for flag in option.flags}


def run_options_text(*, workflow: bool = False) -> str:
    options = [
        option for option in _RUN_OPTIONS if not workflow or set(option.flags) <= _WORKFLOW_FLAGS
    ]
    column = 2 + max(len(option.label) for option in _RUN_OPTIONS) + 2
    lines: list[str] = []
    for option in options:
        wrapped = textwrap.wrap(option.help, _HELP_WIDTH - column)
        lines.append(f"  {option.label}".ljust(column) + wrapped[0])
        lines.extend(" " * column + line for line in wrapped[1:])
    return "\n".join(lines)


def selector_usage(selector: str, *, workflow: bool) -> str:
    return (_WORKFLOW_USAGE if workflow else _RUN_USAGE).format(selector=selector)


def selector_help(header: str, selector: str, *, workflow: bool) -> str:
    usage = selector_usage(selector, workflow=workflow)
    return f"{header}\n\nusage: {usage}\n\nrun options:\n{run_options_text(workflow=workflow)}\n"


_OPTION_SETTINGS = frozenset(field.name for field in fields(Options))


def _run_option_setting(option: _RunOption) -> str | None:
    flag = option.flags[-1]
    tri_state = _TRI_STATE_FLAGS.get(flag)
    setting = tri_state[0] if tri_state is not None else _RUN_VALUE_FLAGS.get(flag)
    return setting if setting in _OPTION_SETTINGS else None


def _run_option_metavar(option: _RunOption) -> str | None:
    last = option.flags[-1]
    return option.label[option.label.rindex(last) + len(last) :].lstrip(" =") or None


def _run_option_record(option: _RunOption, supports: Callable[[str], bool]) -> dict[str, object]:
    setting = _run_option_setting(option)
    return {
        "flags": list(option.flags),
        "metavar": _run_option_metavar(option),
        "setting": setting,
        "supported": setting is None or supports(setting),
        "description": option.help,
    }


def run_option_records(
    *, workflow: bool = False, supports: Callable[[str], bool] = lambda _setting: True
) -> list[dict[str, object]]:
    return [
        _run_option_record(option, supports)
        for option in _RUN_OPTIONS
        if not workflow or set(option.flags) <= _WORKFLOW_FLAGS
    ]


_EPILOG = f"""run syntax:
  {_RUN_USAGE.format(selector="SELECTOR")}

SELECTOR is an agent name (codex, kiro), alias (cx), profile, or workflow. prat list lists
them; prat SELECTOR --help lists the options that SELECTOR takes.
Redirected stdin is the prompt when no other source is given; --context - adds it to one.

run options:
{run_options_text()}

examples:
  prat codex "review this change"
  prat simple "review this change" --effort low
  printf 'multiline prompt\\n' | prat cc -
  prat cc --file prompt.md
  prat cc --prompt=-leading-dash
  prat cx "how many Rs in strawberry" | prat cx --context - "times 5"
  prat cc -t rename --param old=foo --param new=bar
  prat ship "add a --verbose flag"
  prat cx --json --context - "summarize this diff" < change.diff
"""


@dataclass(frozen=True)
class RunArguments:
    selector: str
    prompt_source: PromptSource | None
    config: str | None
    cwd: str | None
    json: bool
    dry_run: bool
    options: Options
    progress: bool
    trace: bool
    template: str | None = None
    contexts: tuple[str, ...] = ()
    extract: bool = False
    edit: bool = False
    words: tuple[str, ...] = ()
    parameters: tuple[str, ...] = ()


@dataclass(frozen=True)
class _RunToken:
    argument: str
    name: str
    value: str | None = None
    refused: str | None = None


@dataclass(frozen=True)
class _RunScan:
    tokens: tuple[_RunToken, ...]
    native_arguments: tuple[str, ...] | None


class Parser(argparse.ArgumentParser):
    def __init__(self, *, add_help: bool = True, **kwargs: Any) -> None:
        self.flags: list[str] = []
        self.documented: list[argparse.Action] = []
        self.listed_choices: dict[str, tuple[str, ...]] = {}
        super().__init__(add_help=False, **kwargs)
        if add_help:
            self.add_argument(
                "-h",
                "--help",
                action="help",
                default=argparse.SUPPRESS,
                help="Show this help and exit.",
            )

    def add_argument(self, *args: Any, **kwargs: Any) -> argparse.Action:
        listed_choices = kwargs.pop("listed_choices", None)
        action = super().add_argument(*args, **kwargs)
        if action.help != argparse.SUPPRESS:
            self.flags.extend(action.option_strings)
            self.documented.append(action)
        if listed_choices is not None:
            self.listed_choices[action.dest] = tuple(listed_choices)
        return action

    def add_subparsers(self, **kwargs: Any) -> "argparse._SubParsersAction[Any]":
        action = super().add_subparsers(**kwargs)
        self.documented.append(action)
        return action

    def error(self, message: str) -> NoReturn:
        raise PratError(message, code="invalid_arguments")


def unrecognized_argument(argument: str, flags: Iterable[str]) -> PratError:
    suggestion = did_you_mean(argument.partition("=")[0], flags) if argument[:1] == "-" else ""
    return PratError(f"unrecognized argument: {argument}{suggestion}", code="invalid_arguments")


_CONFIG_HELP = "Merge PATH over global config instead of .pratfile (relative to invocation cwd)."


def _common_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config",
        metavar="PATH",
        default=argparse.SUPPRESS,
        help=_CONFIG_HELP,
    )
    parser.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Print a JSON management result.",
    )


def build_parser() -> Parser:
    return _parsers()[()]


def parse_management(arguments: list[str]) -> argparse.Namespace:
    _check_config_command(arguments)
    parsers = _parsers()
    args, extras = parsers[()].parse_known_args(arguments)
    if extras:
        command = (args.command, getattr(args, "config_command", None))
        path = tuple(name for name in command if name is not None)
        raise unrecognized_argument(extras[0], parsers[path].flags)
    return args


def _help_parser(path: tuple[str, ...]) -> Parser:
    parsers = _parsers()
    if path[:1] == ("config",) and len(path) > 1:
        _check_config_command(list(path))
    if path not in parsers:
        raise unrecognized_argument(path[-1], ())
    return parsers[path]


def command_help(path: tuple[str, ...]) -> str:
    return _help_parser(path).format_help()


def command_help_page(path: tuple[str, ...]) -> dict[str, object]:
    parser = _help_parser(path)
    usage = " ".join(parser.format_usage().removeprefix("usage: ").split())
    if not path:
        return {
            "kind": "prat",
            "usage": usage,
            "commands": [
                {"name": name, "description": description}
                for name, description in _MANAGEMENT_HELP.items()
            ],
            "run_options": run_option_records(),
        }
    return {
        "kind": "command",
        "name": " ".join(path),
        "description": parser.description,
        "usage": usage,
        "options": [
            _option_record(action) for action in parser.documented if action.option_strings
        ],
        "arguments": [
            _argument_record(parser, action)
            for action in parser.documented
            if not action.option_strings
        ],
    }


def _option_record(action: argparse.Action) -> dict[str, object]:
    metavar = None if action.nargs == 0 else action.metavar or action.dest.upper()
    return {"flags": list(action.option_strings), "metavar": metavar, "description": action.help}


def _argument_record(parser: Parser, action: argparse.Action) -> dict[str, object]:
    choices = (
        list(action.choices)
        if action.choices is not None
        else parser.listed_choices.get(action.dest)
    )
    return {
        "name": action.metavar if isinstance(action.metavar, str) else "COMMAND",
        "choices": None if choices is None else list(choices),
        "description": action.help,
    }


def check_list_section(section: str | None) -> None:
    if section is None or section in LIST_SECTIONS:
        return
    choices = ", ".join(LIST_SECTIONS)
    raise PratError(
        f"unknown section {section!r}{did_you_mean(section, LIST_SECTIONS)} (choose from {choices})",
        code="invalid_arguments",
    )


def _check_config_command(arguments: list[str]) -> None:
    tokens = _scan_run(arguments).tokens
    if any(token.argument in {"-h", "--help"} for token in tokens):
        return
    words = [token.argument for token in tokens if token.argument[:1] != "-"]
    if words[:1] != ["config"]:
        return
    choices = ", ".join(_CONFIG_COMMAND_HELP)
    if len(words) == 1:
        raise PratError(
            f"config requires a command (choose from {choices})", code="invalid_arguments"
        )
    if words[1] not in _CONFIG_COMMAND_HELP:
        suggestion = did_you_mean(words[1], _CONFIG_COMMAND_HELP)
        raise PratError(
            f"unknown config command {words[1]!r}{suggestion} (choose from {choices})",
            code="invalid_arguments",
        )


def _parsers() -> dict[tuple[str, ...], Parser]:
    parser = Parser(
        prog="prat",
        description="One-shot coding agents for scripts and pipes.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_EPILOG,
        allow_abbrev=False,
    )
    parser.set_defaults(config=None, json=False)
    _common_flags(parser)
    parser.add_argument(
        "--version",
        action="version",
        version=f"prat {prat_version()}",
        help="Show the version and exit.",
    )
    subparsers = parser.add_subparsers(dest="command", parser_class=Parser)
    parsers: dict[tuple[str, ...], Parser] = {(): parser}
    listing = _command_parser(subparsers, ("list",), _MANAGEMENT_HELP["list"])
    parsers[("list",)] = listing
    _common_flags(listing)
    listing.add_argument(
        "section",
        nargs="?",
        metavar="SECTION",
        help="profiles, workflows, templates or agents; default: every section.",
        listed_choices=LIST_SECTIONS,
    )
    doctor = _command_parser(subparsers, ("doctor",), _MANAGEMENT_HELP["doctor"])
    parsers[("doctor",)] = doctor
    _common_flags(doctor)
    doctor.add_argument(
        "--versions",
        action="store_true",
        help=(
            "Execute each available configured command prefix with its native version "
            "arguments (version for Amp, --version for other agents); "
            "configured wrappers may have side effects."
        ),
    )
    config = _command_parser(subparsers, ("config",), _MANAGEMENT_HELP["config"])
    parsers[("config",)] = config
    _common_flags(config)
    config_commands = config.add_subparsers(
        dest="config_command", required=True, parser_class=Parser
    )
    for name, help_text in _CONFIG_COMMAND_HELP.items():
        child = _command_parser(config_commands, ("config", name), help_text)
        parsers[("config", name)] = child
        _common_flags(child)
    trust = _command_parser(subparsers, ("trust",), _MANAGEMENT_HELP["trust"])
    parsers[("trust",)] = trust
    trust.add_argument("--config", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    trust.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Print a JSON management result.",
    )
    trust.add_argument(
        "directory",
        nargs="?",
        metavar="DIR",
        help="Directory to trust or revoke; default: the invocation directory.",
    )
    trust.add_argument("--list", action="store_true", help="List trusted directories.")
    trust.add_argument(
        "--revoke", action="store_true", help="Remove DIR's trust entry; DIR need not exist."
    )
    completion = _command_parser(subparsers, ("completion",), _MANAGEMENT_HELP["completion"])
    parsers[("completion",)] = completion
    help_parser = _command_parser(subparsers, ("help",), _MANAGEMENT_HELP["help"])
    parsers[("help",)] = help_parser
    help_parser.add_argument(
        "--config", metavar="PATH", default=argparse.SUPPRESS, help=_CONFIG_HELP
    )
    help_parser.add_argument(
        "topic",
        nargs="*",
        metavar="SELECTOR|COMMAND",
        help="Selector, or management command and subcommand, to describe.",
    )
    help_parser.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Print the help page as one JSON object.",
    )
    version_parser = _command_parser(subparsers, ("version",), _MANAGEMENT_HELP["version"])
    parsers[("version",)] = version_parser
    version_parser.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Print a JSON management result.",
    )
    completion.add_argument(
        "shell",
        nargs="?",
        metavar="SHELL",
        help="bash or zsh; default: the basename of $SHELL, else bash.",
        listed_choices=SHELLS,
    )
    return parsers


def _command_parser(
    subparsers: "argparse._SubParsersAction[Parser]", path: tuple[str, ...], help_text: str
) -> Parser:
    return subparsers.add_parser(
        path[-1],
        help=help_text,
        description=help_text,
        epilog=_examples_text(_COMMAND_EXAMPLES[path]),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        allow_abbrev=False,
    )


def _examples_text(examples: tuple[str, ...]) -> str:
    heading = "example" if len(examples) == 1 else "examples"
    return "\n".join((f"{heading}:", *(f"  {example}" for example in examples)))


def _parse_run(arguments: list[str]) -> RunArguments:
    scan = _scan_run(arguments)
    values: dict[str, str] = {}
    contexts: list[str] = []
    repeated: dict[str, list[str]] = {
        "add_dirs": [],
        "attachments": [],
        "tools": [],
        "disabled_tools": [],
        "parameters": [],
    }
    selector: str | None = None
    words: list[str] = []
    prompt_source: PromptSource | None = None
    switches = {token.argument for token in scan.tokens} & _RUN_SWITCHES
    dry_run = "--dry-run" in switches
    edit = bool(switches & {"-e", "--edit"})
    tri_state: dict[str, bool | None] = {"fast": None, "notify": None}
    for token in scan.tokens:
        argument = token.argument
        if argument in _RUN_SWITCHES:
            continue
        tri_state_flag = _TRI_STATE_FLAGS.get(argument)
        if tri_state_flag is not None:
            key, flag_value = tri_state_flag
            tri_state[key] = flag_value
            continue
        field = _RUN_VALUE_FLAGS.get(token.name)
        if field is not None:
            value = _run_token_value(token)
            if field == "prompt":
                prompt_source = _add_prompt_source(prompt_source, PromptSource("inline", value))
            elif field == "file":
                prompt_source = _add_prompt_source(
                    prompt_source, PromptSource("file", _text_option(value, token.name))
                )
            elif field in {*repeated, "context"}:
                _text_option(value if field != "parameters" else None, token.name)
                (contexts if field == "context" else repeated[field]).append(value)
            else:
                _store_value(values, field, value)
            continue
        if argument.startswith("-") and argument != "-":
            raise unrecognized_argument(argument, _RUN_FLAG_NAMES)
        if selector is None:
            selector = argument
        else:
            words.append(argument)
    if selector is None:
        raise PratError(SELECTOR_REQUIRED, code="invalid_arguments")
    if edit and dry_run:
        raise PratError("--edit cannot be combined with --dry-run", code="invalid_arguments")
    options = _run_options(
        values, tri_state["fast"], tri_state["notify"], repeated, scan.native_arguments
    )
    return RunArguments(
        selector,
        prompt_source,
        _text_option(values.get("config"), "--config"),
        _text_option(values.get("cwd"), "--cwd"),
        "--json" in switches,
        dry_run,
        options,
        "--progress" in switches,
        "--trace" in switches,
        _text_option(values.get("template"), "--template"),
        tuple(contexts),
        bool(switches & {"-x", "--extract"}),
        edit,
        tuple(words),
        tuple(repeated["parameters"]),
    )


def _run_options(
    values: dict[str, str],
    fast: bool | None,
    notify: bool | None,
    repeated: dict[str, list[str]],
    native_arguments: tuple[str, ...] | None,
) -> Options:
    if "instructions" in values and "instructions_file" in values:
        raise PratError(
            "--instructions and --instructions-file cannot be used together",
            code="invalid_arguments",
        )
    return Options(
        schema=_text_option(values.get("schema"), "--schema"),
        instructions=(
            validate_instructions(
                values["instructions"], OptionOrigin("--instructions", "invalid_arguments")
            )
            if "instructions" in values
            else None
        ),
        instructions_file=_text_option(values.get("instructions_file"), "--instructions-file"),
        model=_text_option(values.get("model"), "--model"),
        effort=_text_option(values.get("effort"), "--effort"),
        timeout=_number_option(values.get("timeout"), "--timeout"),
        max_budget_usd=_number_option(values.get("max_budget_usd"), "--max-budget-usd"),
        max_turns=_integer_option(values.get("max_turns"), "--max-turns"),
        max_ai_credits=_number_option(values.get("max_ai_credits"), "--max-ai-credits"),
        fast=fast,
        add_dirs=tuple(repeated["add_dirs"]) or None,
        attachments=tuple(repeated["attachments"]) or None,
        tools=tuple(repeated["tools"]) or None,
        disabled_tools=tuple(repeated["disabled_tools"]) or None,
        native_agent=(
            validate_native_agent(
                values["native_agent"], OptionOrigin("--native-agent", "invalid_arguments")
            )
            if "native_agent" in values
            else None
        ),
        session_id=(
            validate_session_id(
                values["session_id"], OptionOrigin("--session-id", "invalid_arguments")
            )
            if "session_id" in values
            else None
        ),
        native_args=native_arguments,
        notify=notify,
    )


def _store_value(values: dict[str, str], field: str, value: str) -> None:
    if field == "template" and field in values:
        raise PratError("provide only one template", code="invalid_arguments")
    values[field] = value


def _scan_run(arguments: list[str]) -> _RunScan:
    if "--" in arguments:
        delimiter = arguments.index("--")
        prat_arguments = arguments[:delimiter]
        native_arguments: tuple[str, ...] | None = tuple(arguments[delimiter + 1 :])
    else:
        prat_arguments = arguments
        native_arguments = None
    tokens: list[_RunToken] = []
    index = 0
    while index < len(prat_arguments):
        argument = prat_arguments[index]
        name, equals, inline = argument.partition("=")
        if name not in _RUN_VALUE_FLAGS:
            tokens.append(_RunToken(argument, name))
            index += 1
            continue
        if equals:
            tokens.append(_RunToken(argument, name, inline))
            index += 1
            continue
        value = prat_arguments[index + 1] if index + 1 < len(prat_arguments) else None
        if value is not None and value.startswith("-") and value != "-":
            tokens.append(_RunToken(argument, name, refused=value))
            index += 1
            continue
        tokens.append(_RunToken(argument, name, value))
        index += 2
    return _RunScan(tuple(tokens), native_arguments)


def _run_token_value(token: _RunToken) -> str:
    if token.refused is not None:
        raise PratError(
            f"{token.name} requires a value; {token.refused!r} is not a value; "
            f"write {token.name}={token.refused} if it is",
            code="invalid_arguments",
        )
    if token.value is None:
        raise PratError(f"{token.name} requires a value", code="invalid_arguments")
    return token.value


def _add_prompt_source(current: PromptSource | None, added: PromptSource) -> PromptSource:
    if current is not None:
        raise PratError("provide exactly one prompt source", code="invalid_arguments")
    return added


def _text_option(value: str | None, flag: str) -> str | None:
    if value is not None and (not value.strip() or "\0" in value):
        raise PratError(f"{flag} requires a nonempty value", code="invalid_arguments")
    return value


def _number_option(value: str | None, flag: str) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except ValueError:
        number = math.nan
    if not math.isfinite(number) or number <= 0:
        raise PratError(
            f"{flag} {value!r}: expected a finite positive number", code="invalid_arguments"
        )
    return number


def _integer_option(value: str | None, flag: str) -> int | None:
    if value is None:
        return None
    try:
        number = int(value)
    except ValueError:
        number = 0
    if str(number) != value or not 0 < number <= MAX_TURNS:
        raise PratError(f"{flag} {value!r}: expected a positive integer", code="invalid_arguments")
    return number


def _run_cwd(value: str | None, invocation_cwd: Path) -> Path:
    if value is None:
        return invocation_cwd
    try:
        path = Path(os.path.abspath(invocation_cwd / Path(value).expanduser()))
    except RuntimeError as error:
        raise PratError(
            f"--cwd cannot expand {value!r}; use an absolute path or a valid ~user path",
            code="invalid_arguments",
        ) from error
    if not path.is_dir():
        raise PratError(f"--cwd is not a directory: {path}", code="invalid_arguments")
    return path


@dataclass(frozen=True)
class HelpRequest:
    selector: str | None
    config: str | None
    json: bool = False


def help_request(arguments: list[str]) -> HelpRequest | None:
    tokens = _scan_run(arguments).tokens
    if not any(token.argument in {"-h", "--help"} for token in tokens):
        return None
    words = [
        token.argument
        for token in tokens
        if token.name not in _RUN_VALUE_FLAGS and token.argument[:1] != "-"
    ]
    config = next((token.value for token in tokens if token.name == "--config"), None)
    json = any(token.argument == "--json" for token in tokens)
    return HelpRequest(words[0] if words else None, config, json)


def json_help_path(arguments: list[str]) -> tuple[str, ...] | None:
    tokens = _scan_run(arguments).tokens
    flags = {token.argument for token in tokens}
    if "--json" not in flags or not flags & {"-h", "--help"}:
        return None
    words = [
        token.argument
        for token in tokens
        if token.name not in _RUN_VALUE_FLAGS and token.argument[:1] != "-"
    ]
    return tuple(words[:2] if words[:1] == ["config"] else words[:1])


def version_json_requested(arguments: list[str]) -> bool:
    flags = {token.argument for token in _scan_run(arguments).tokens}
    return {"--version", "--json"} <= flags and all(flag[:1] == "-" for flag in flags)


def prat_version() -> str:
    return version("pratfall")


def _json_requested(arguments: list[str]) -> bool:
    return any(token.argument == "--json" for token in _scan_run(arguments).tokens)


def _management_mode(arguments: list[str]) -> bool:
    run_option = False
    for token in _scan_run(arguments).tokens:
        argument = token.argument
        if token.name in _RUN_VALUE_FLAGS:
            run_option = run_option or token.name != "--config"
            continue
        if argument in _RUN_FLAGS_WITHOUT_VALUE:
            run_option = run_option or argument != "--json"
            continue
        if argument.startswith("-"):
            continue
        return argument in MANAGEMENT_COMMANDS
    return not run_option
