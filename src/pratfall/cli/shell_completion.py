import os
import re
import shlex
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pratfall.catalog import AGENTS, BY_NAME, BY_SELECTOR, MANAGEMENT_COMMANDS
from pratfall.cli.parsing import (
    _CONFIG_COMMAND_HELP,
    _EPILOG,
    _MANAGEMENT_HELP,
    _RUN_FLAGS_WITHOUT_VALUE,
    _RUN_VALUE_FLAGS,
    _management_mode,
    _scan_run,
)
from pratfall.config import load_config
from pratfall.errors import PratError
from pratfall.models import Config
from pratfall.prompt_templates import template_parameters
from pratfall.workflow_plan import workflow_parameter_names

SHELLS = ("bash", "zsh")

_BASH_SCRIPT = r"""_prat() {
    local cur=${COMP_WORDS[COMP_CWORD]} tab=$'\t' line
    local -a reply=()
    while IFS= read -r line; do reply+=("$line"); done < <(
        command prat __complete "${COMP_WORDS[@]:1:COMP_CWORD}" 2>/dev/null </dev/null
    )
    COMPREPLY=()
    case ${reply[0]-} in
        dirs)
            compopt -o filenames 2>/dev/null
            while IFS= read -r line; do COMPREPLY+=("$line"); done < <(compgen -d -- "$cur")
            ;;
        words)
            compopt +o default 2>/dev/null
            for line in "${reply[@]:1}"; do COMPREPLY+=("${line%%${tab}*}"); done
            if [[ ${#COMPREPLY[@]} -eq 1 && ${COMPREPLY[0]} == *= ]]; then
                compopt -o nospace 2>/dev/null
            fi
            ;;
    esac
}
complete -o default -F _prat prat
"""

_ZSH_SCRIPT = r"""#compdef prat
_prat() {
    local -a reply plain suffixed
    local line
    reply=("${(@f)$(command prat __complete "${(@Q)words[2,CURRENT]}" 2>/dev/null </dev/null)}")
    case ${reply[1]-} in
        files) _files ;;
        dirs) _files -/ ;;
        words)
            for line in "${(@)reply[2,-1]}"; do
                line=${line/$'\t'/:}
                if [[ ${line%%:*} == *= ]]; then suffixed+=("$line"); else plain+=("$line"); fi
            done
            (( $#plain )) && _describe -t prat prat plain
            (( $#suffixed )) && _describe -t prat prat suffixed -S ''
            ;;
    esac
    return 0
}
if [[ $zsh_eval_context == *func ]]; then _prat "$@"; else compdef _prat prat; fi
"""

_DIR_FLAGS = frozenset({"--cwd", "--add-dir"})
_FILE_FLAGS = frozenset(
    {"-f", "--file", "--context", "--schema", "--attach", "--instructions-file", "--config"}
)
_TEMPLATE_FLAGS = frozenset({"-t", "--template"})
_TOP_LEVEL_FLAGS = {
    "-h": "Show help and exit.",
    "--help": "Show help and exit.",
    "--version": "Show the version and exit.",
}
_MANAGEMENT_FLAGS = {
    "--config": "Merge PATH over global config instead of .pratfile.",
    "--json": "Print a JSON management result.",
    "-h": "Show help and exit.",
    "--help": "Show help and exit.",
}

Mode = Literal["words", "files", "dirs"]
Candidate = tuple[str, str]


@dataclass(frozen=True)
class _Completion:
    mode: Mode
    candidates: tuple[Candidate, ...] = ()


def _completion_script(shell: str | None, environ: Mapping[str, str]) -> str:
    chosen = shell or Path(environ.get("SHELL", "")).name or "bash"
    if chosen == "zsh":
        return _ZSH_SCRIPT
    if chosen == "bash":
        return _BASH_SCRIPT
    raise PratError(
        f"Unsupported shell {chosen!r}; run 'prat completion bash' or 'prat completion zsh'.",
        code="invalid_arguments",
    )


def _complete_command(words: Sequence[str]) -> int:
    try:
        result = _complete(words, Path.cwd())
    except Exception:  # noqa: BLE001 - completion must stay silent and never fail the shell
        result = _Completion("words")
    lines = [
        result.mode,
        *(f"{word}\t{help}" if help else word for word, help in result.candidates),
    ]
    sys.stdout.write("\n".join(lines) + "\n")
    return 0


def _complete(words: Sequence[str], cwd: Path) -> _Completion:
    *preceding, current = _rejoin_wordbreaks(words) or [""]
    scan = _scan_run(preceding)
    if scan.native_arguments is not None:
        return _Completion("files")
    last = scan.tokens[-1] if scan.tokens else None
    if last is not None and last.name in _RUN_VALUE_FLAGS and last.value is None:
        return _value_slot(last.name, preceding, current, cwd)
    if current.startswith("-") and "=" in current:
        return _Completion("words")
    positionals = _positionals(preceding)
    if not positionals:
        if current.startswith("-"):
            return _words(current, {**_run_flags(), **_TOP_LEVEL_FLAGS}.items())
        management = _management_mode(preceding)
        return _words(current, _selectors(preceding, cwd, management=management))
    if positionals[0] in MANAGEMENT_COMMANDS and _management_mode(preceding):
        return _management(positionals[0], positionals[1:], current)
    if current.startswith("-"):
        return _words(current, _run_flags().items())
    return _Completion("files" if _unbound_parameters(preceding, cwd) else "words")


def _rejoin_wordbreaks(words: Sequence[str]) -> list[str]:
    joined: list[str] = []
    open_value = False
    for word in words:
        previous = joined[-1] if joined else ""
        if word == "=" and not open_value and _attaches_equals(joined):
            joined[-1] = f"{previous}="
            open_value = True
        elif open_value:
            joined[-1] = f"{previous}{word}"
            open_value = False
        else:
            joined.append(word)
    return joined


def _attaches_equals(joined: list[str]) -> bool:
    previous = joined[-1] if joined else ""
    return previous.startswith("-") or (len(joined) > 1 and joined[-2] == "--param")


def _positionals(arguments: list[str]) -> list[str]:
    return [
        token.argument
        for token in _scan_run(arguments).tokens
        if token.name not in _RUN_VALUE_FLAGS
        and (token.argument == "-" or not token.argument.startswith("-"))
    ]


def _value_slot(flag: str, preceding: list[str], current: str, cwd: Path) -> _Completion:
    if flag in _DIR_FLAGS:
        return _Completion("dirs")
    if flag in _FILE_FLAGS:
        return _Completion("files")
    if flag in _TEMPLATE_FLAGS:
        config = _load(preceding, cwd)
        templates = config.templates if config is not None else {}
        return _words(current, ((name, "template") for name in templates))
    if flag == "--param":
        if "=" in current:
            return _Completion("words")
        names = _unbound_parameters(preceding, cwd)
        return _words(current, ((f"{name}=", "parameter") for name in names))
    if flag == "--effort":
        return _words(current, ((value, "") for value in _efforts(preceding, cwd)))
    return _Completion("words")


def _unbound_parameters(preceding: list[str], cwd: Path) -> list[str]:
    by_position = max(len(_positionals(preceding)) - 1, 0)
    by_name = {
        token.value.partition("=")[0]
        for token in _scan_run(preceding).tokens
        if token.name == "--param" and token.value is not None
    }
    return [
        name for name in _active_parameters(preceding, cwd)[by_position:] if name not in by_name
    ]


def _active_parameters(preceding: list[str], cwd: Path) -> tuple[str, ...]:
    config = _load(preceding, cwd)
    if config is None:
        return ()
    names = [
        token.value
        for token in _scan_run(preceding).tokens
        if token.name in _TEMPLATE_FLAGS and token.value is not None
    ]
    if names:
        template = config.templates.get(names[-1])
        return template_parameters(template.prompt) if template is not None else ()
    positionals = _positionals(preceding)
    workflow = config.workflows.get(positionals[0]) if positionals else None
    if workflow is not None:
        return workflow_parameter_names(config, workflow)
    profile = config.profiles.get(positionals[0]) if positionals else None
    if profile is None or profile.prompt is None:
        return ()
    return template_parameters(profile.prompt.prompt)


def _efforts(preceding: list[str], cwd: Path) -> tuple[str, ...]:
    positionals = _positionals(preceding)
    if positionals and positionals[0] in BY_SELECTOR:
        return BY_SELECTOR[positionals[0]].capabilities.effort_values
    config = _load(preceding, cwd) if positionals else None
    if config is not None and positionals[0] in config.profiles:
        return BY_NAME[config.profiles[positionals[0]].agent].capabilities.effort_values
    return tuple(dict.fromkeys(v for agent in AGENTS for v in agent.capabilities.effort_values))


def _selectors(preceding: list[str], cwd: Path, *, management: bool) -> list[Candidate]:
    candidates = [
        (selector, agent.label) for agent in AGENTS for selector in (agent.name, *agent.aliases)
    ]
    config = _load(preceding, cwd)
    if config is not None:
        candidates.extend(
            (name, f"profile: {profile.agent}") for name, profile in config.profiles.items()
        )
        candidates.extend((name, "workflow") for name in config.workflows)
    if management:
        candidates.extend(_MANAGEMENT_HELP.items())
    return candidates


def _management(command: str, rest: list[str], current: str) -> _Completion:
    if current.startswith("-"):
        if command == "completion":
            flags = {key: _MANAGEMENT_FLAGS[key] for key in ("-h", "--help")}
        elif command == "doctor":
            flags = {**_MANAGEMENT_FLAGS, "--versions": "Also run each agent's version command."}
        else:
            flags = _MANAGEMENT_FLAGS
        return _words(current, flags.items())
    if not rest and command == "config":
        return _words(current, _CONFIG_COMMAND_HELP.items())
    if not rest and command == "completion":
        return _words(current, ((shell, "") for shell in SHELLS))
    return _Completion("words")


def _load(preceding: list[str], cwd: Path) -> Config | None:
    explicit = None
    for token in _scan_run(preceding).tokens:
        if token.name == "--config" and token.value is not None:
            explicit = token.value
    try:
        if explicit is not None:
            explicit = os.path.expanduser(shlex.split(explicit)[0])
        return load_config(explicit, cwd=cwd)
    except Exception:  # noqa: BLE001 - an unreadable config only hides its names
        return None


def _run_flags() -> dict[str, str]:
    flags = [*_RUN_VALUE_FLAGS, *_RUN_FLAGS_WITHOUT_VALUE, "--prompt="]
    help_text = _run_flag_help()
    return {flag: help_text.get(flag, "") for flag in flags}


def _run_flag_help() -> dict[str, str]:
    section = _EPILOG.split("run options:\n", 1)[1].split("\n\n", 1)[0]
    help_text: dict[str, str] = {}
    for line in section.splitlines():
        match = re.fullmatch(r"  (-\S.*?)\s{2,}(\S.*)", line)
        if match is not None:
            for flag in re.findall(r"--?[a-z][\w-]*=?", match[1]):
                help_text[flag] = match[2]
    return help_text


def _words(current: str, candidates: Iterable[Candidate]) -> _Completion:
    matching = {word: help for word, help in candidates if word.startswith(current)}
    return _Completion("words", tuple(sorted(matching.items())))
