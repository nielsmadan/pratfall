import argparse
import json
import shutil
import sys
from collections.abc import Sequence
from dataclasses import asdict
from typing import NoReturn

from pratfall import __version__
from pratfall.catalog import AGENTS
from pratfall.config import config_path, init_config, load_config, resolve_profile
from pratfall.errors import PratError
from pratfall.models import Config


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise PratError(message, code="invalid_arguments")


def _common_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config",
        metavar="PATH",
        default=argparse.SUPPRESS,
        help="Use this config path (relative to invocation cwd).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Print a JSON management result.",
    )


def build_parser() -> Parser:
    parser = Parser(
        prog="prat",
        description="Run installed coding agents through named profiles.",
        allow_abbrev=False,
    )
    parser.set_defaults(config=None, json=False)
    _common_flags(parser)
    parser.add_argument("--version", action="version", version=f"prat {__version__}")
    subparsers = parser.add_subparsers(dest="command", parser_class=Parser)
    for command, help_text in (
        ("agents", "List built-in agent selectors and supported settings."),
        ("profiles", "List configured profiles and their resolved settings."),
        ("doctor", "Locate agent executables without running them or checking credentials."),
    ):
        child = subparsers.add_parser(command, help=help_text, allow_abbrev=False)
        _common_flags(child)
    config = subparsers.add_parser(
        "config", help="Manage the global TOML config.", allow_abbrev=False
    )
    _common_flags(config)
    config_commands = config.add_subparsers(
        dest="config_command", required=True, parser_class=Parser
    )
    for name, help_text in (
        ("path", "Print the selected config path."),
        ("init", "Create an example config; fail if the path already exists."),
        ("validate", "Validate every configured profile without launching an agent."),
    ):
        child = config_commands.add_parser(name, help=help_text, allow_abbrev=False)
        _common_flags(child)
    return parser


def _emit(payload: dict[str, object], lines: list[str], *, json_mode: bool) -> None:
    if json_mode:
        print(json.dumps({"schema_version": 1, **payload}, ensure_ascii=False))
    else:
        for line in lines:
            print(line)


def _agents(json_mode: bool) -> None:
    records = []
    lines = []
    for agent in AGENTS:
        caps = agent.capabilities
        capabilities = {
            "model": caps.model,
            "effort": caps.effort,
            "effort_values": list(caps.effort_values) or None,
            "budgets": sorted(caps.budgets),
        }
        records.append(
            {
                "name": agent.name,
                "label": agent.label,
                "aliases": list(agent.aliases),
                "command": list(agent.command),
                "capabilities": capabilities,
            }
        )
        supported = [field for field in ("model", "effort") if getattr(caps, field)]
        supported.extend(sorted(caps.budgets))
        lines.append(f"{agent.name} ({', '.join(agent.aliases)}): {', '.join(supported) or 'none'}")
    _emit({"agents": records}, lines, json_mode=json_mode)


def _profiles(config: Config, json_mode: bool) -> None:
    records = []
    lines = []
    for name in sorted(config.profiles):
        resolved = resolve_profile(config, name)
        options = asdict(resolved.options)
        records.append({"name": name, "agent": resolved.agent.name, "options": options})
        description = f"{name}: {resolved.agent.name}"
        for key, value in options.items():
            if value is None or value == ():
                continue
            rendered = json.dumps(value, ensure_ascii=False) if key == "native_args" else value
            description += f" {key}={rendered}"
        lines.append(description)
    _emit({"profiles": records}, lines or ["No profiles configured."], json_mode=json_mode)


def _doctor(config: Config, json_mode: bool) -> None:
    records = []
    lines = []
    for agent in AGENTS:
        executable = config.commands.get(agent.name, agent.command)[0]
        found = shutil.which(executable)
        records.append(
            {
                "agent": agent.name,
                "executable": executable,
                "path": found,
                "available": found is not None,
            }
        )
        lines.append(
            f"{agent.name}: {found if found is not None else 'unavailable (' + executable + ')'}"
        )
    _emit({"agents": records}, lines, json_mode=json_mode)


def _dispatch(args: argparse.Namespace) -> None:
    if args.command == "config" and args.config_command == "path":
        path = config_path(args.config)
        _emit({"path": str(path)}, [str(path)], json_mode=args.json)
    elif args.command == "config" and args.config_command == "init":
        path = init_config(args.config)
        _emit({"path": str(path), "created": True}, [f"Created {path}"], json_mode=args.json)
    else:
        config = load_config(args.config)
        if args.command == "agents":
            _agents(args.json)
        elif args.command == "profiles":
            _profiles(config, args.json)
        elif args.command == "doctor":
            _doctor(config, args.json)
        else:
            _emit(
                {"path": str(config.path), "exists": config.exists, "valid": True},
                [
                    f"Valid config: {config.path}"
                    if config.exists
                    else f"No config at {config.path}; using built-in defaults."
                ],
                json_mode=args.json,
            )


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    json_arguments = arguments[: arguments.index("--")] if "--" in arguments else arguments
    try:
        parser = build_parser()
        args = parser.parse_args(arguments)
        if args.command is None:
            parser.print_help()
            return 0
        _dispatch(args)
    except PratError as error:
        if "--json" in json_arguments:
            _emit(
                {
                    "status": "error",
                    "exit_code": error.exit_code,
                    "error": {"code": error.code, "message": str(error)},
                },
                [],
                json_mode=True,
            )
        else:
            print(f"prat: {error}", file=sys.stderr)
        return error.exit_code
    except BrokenPipeError:
        return 1
    return 0
