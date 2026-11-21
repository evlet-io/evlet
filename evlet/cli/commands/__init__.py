"""The evlet commands: each module adds its commands' parsers and runs them.

A module lists its commands in COMMANDS as {name: (add, run)}. add(commands) adds the parser to the
subparsers of evlet; run(args, context) carries the command out. evlet.cli.main orders, groups and
dispatches them. Modules import their heavy dependencies inside run, so evlet --help stays fast.
"""

from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Callable


@dataclass(frozen=True)
class Context:
    """What a command needs beyond its arguments."""
    root: Path | None  # The project root; None for commands that run without a project.
    values: list  # The command line as given, for re-running it in the project's .venv.
    console: bool  # Run as the console script (argv=None), so it may continue in the project's .venv.
    confirm: Callable[[str], bool]


INSTANCE_HELP = 'Instance name; --instance NAME also works'


def add_link_options(command):
    command.add_argument('--no-link', action='store_true', help='Do not print a one-time sign-in link at startup')
    command.add_argument('--user', help='Sign the startup link in as this user (default: the built-in Administrator)')


def add_instance_option(command, help='Required when the project has several instances'):
    """--instance NAME, and the same name as an optional positional (evlet dev acme)."""
    command.add_argument('instance_arg', nargs='?', metavar='INSTANCE', help='Instance name; the same as --instance')
    command.add_argument('--instance', help=help)


def choose_instance(root, command):
    names = sorted(path.name for path in (root / 'instances').iterdir() if path.is_dir() and (path / 'config.py').is_file())
    if len(names) == 1:
        return names[0]
    if names and sys.stdin.isatty():
        return input('Choose an instance (' + ', '.join(names) + '): ').strip()
    raise ValueError(f'Select an instance with evlet {command} --instance NAME. Create and install an app first')


def require_instance(root, args):
    """Choose the instance when none was given; the message names the command and its app (evlet install notes)."""
    if not args.instance:
        args.instance = choose_instance(root, args.command + (' ' + (getattr(args, 'name', None) or getattr(args, 'app', None) or '')).rstrip())
