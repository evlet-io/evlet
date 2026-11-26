"""Create apps, manage isolated instances, and use the bundled development tools."""

import argparse
from importlib import import_module
import sqlite3
import subprocess
import sys

from .commands import Context, choose_instance  # noqa: F401  choose_instance: also used by evlet.cli.devlink
from .commands.setup import check_framework_source  # noqa: F401  kept importable from here
from .project import project_root


def confirm(message):
    return sys.stdin.isatty() and input(message + ' [y/N] ').strip().lower() in ('y', 'yes')


# Every command in the order its parser is added, with the module of evlet.cli.commands that adds and runs it.
# The order is the order of argparse's choices, as in "invalid choice" errors.
COMMANDS = (
    ('setup', 'setup'), ('project', 'project'), ('app', 'apps'), ('build', 'apps'), ('instance', 'apps'),
    ('install', 'install'), ('migrate', 'install'), ('makemigration', 'install'), ('serve', 'serve'), ('dev', 'serve'),
    ('restart', 'lifecycle'), ('upgrade', 'lifecycle'), ('routes', 'routing'), ('proxy', 'routing'), ('host', 'routing'), ('sites', 'sites'),
    ('link-user', 'accounts'), ('controls', 'accounts'), ('users', 'accounts'), ('secrets', 'accounts'),
    ('ids', 'checks'), ('activity', 'checks'), ('ledger', 'checks'), ('jobs', 'checks'),
    ('backup', 'backup'), ('restore', 'backup'), ('playground', 'bundled'), ('docs', 'bundled'), ('dev-link', 'bundled'),
)

# Commands that run without a project, so they neither look for one nor continue in its .venv.
WITHOUT_PROJECT = ('setup', 'docs', 'playground', 'dev-link')


def command_entry(name):
    """The (add, run) pair of a command."""
    return import_module('.commands.' + dict(COMMANDS)[name], __package__).COMMANDS[name]

# Commands whose instance is a positional argument, with the words that name them. `--instance NAME` is moved
# into that position before parsing, so both forms work everywhere (evlet serve acme, evlet serve --instance acme).
POSITIONAL_INSTANCE = {
    'serve': 1, 'migrate': 1, 'restart': 1, 'upgrade': 1, 'routes': 1, 'proxy': 1, 'link-user': 1,
    'controls': 2, 'users': 2, 'secrets': 2, 'ids': 2, 'activity': 2, 'host': 2, 'sites': 2,
}

# evlet --help lists the commands in these groups.
GROUPS = (
    ('Start', ('setup', 'project', 'app', 'instance', 'install')),
    ('Develop', ('dev', 'dev-link', 'playground', 'docs', 'build')),
    ('Change', ('makemigration', 'migrate', 'upgrade')),
    ('Operate', ('serve', 'restart', 'routes', 'proxy', 'host', 'sites', 'link-user', 'jobs', 'ledger', 'backup')),
    ('Recover', ('restore', 'users', 'controls', 'secrets', 'ids', 'activity')),
    ('Connector', ('login', 'logout', 'describe', 'mcp')),
)


def move_instance_option(values):
    """Rewrite `--instance NAME` (or --instance=NAME) into the positional slot of commands that take it there."""
    values = list(values)
    words, index = [], 0
    while index < len(values) and len(words) < 2:
        value = values[index]
        if value == '--project':
            index += 2
            continue
        if not value.startswith('-'):
            words.append((index, value))
            if POSITIONAL_INSTANCE.get(words[0][1]) == len(words):
                break
            if words[0][1] not in POSITIONAL_INSTANCE:
                return values
        index += 1
    if not words or POSITIONAL_INSTANCE.get(words[0][1]) != len(words):
        return values
    position = words[-1][0] + 1
    for at in range(position, len(values)):
        value = values[at]
        if value == '--':
            break
        if value == '--instance' and at + 1 < len(values):
            name = values[at + 1]
            del values[at:at + 2]
        elif value.startswith('--instance='):
            name = value.split('=', 1)[1]
            del values[at]
        else:
            continue
        values.insert(position, name)
        break
    return values


class ShowVersion(argparse.Action):
    """evlet --version: this evlet, and the Evlet in the project's .venv when run inside a project."""

    def __init__(self, option_strings, dest, **values):
        super().__init__(option_strings, dest, nargs=0, default=argparse.SUPPRESS, help='Print the Evlet version and exit')

    def __call__(self, parser, namespace, values, option_string=None):
        from ..framework import version
        from .setup import environment_version
        lines = [f'evlet {version()}']
        try:
            root = project_root(getattr(namespace, 'project', None))
        except (ValueError, OSError):
            root = None
        found = environment_version(root) if root is not None else None
        if found:
            lines.append(f'Project {root}: Evlet {found} in .venv')
        print('\n'.join(lines))
        parser.exit()



def parser():
    result = argparse.ArgumentParser(prog='evlet', description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    result.add_argument('--project', help='Project path; also accepted after the command')
    result.add_argument('--version', action=ShowVersion)
    commands = result.add_subparsers(dest='command', required=True, metavar='COMMAND')
    for name, _ in COMMANDS:
        add, _ = command_entry(name)
        add(commands)
    for command in commands.choices.values():
        command.add_argument('--project', default=argparse.SUPPRESS, help='Explicit project path')
    from .connector import add_commands
    add_commands(commands)
    grouped_help(result, commands)
    return result

def grouped_help(result, commands):
    """List the commands of evlet --help in GROUPS instead of one flat list."""
    helps = {action.dest: action.help or '' for action in commands._choices_actions}
    listed = [name for _, names in GROUPS for name in names]
    if sorted(listed) != sorted(helps):
        raise RuntimeError('evlet --help groups do not list every command: ' + ', '.join(sorted(set(helps) ^ set(listed))))
    width = max(map(len, listed))
    result.epilog = '\n\n'.join(f'{title}:\n' + '\n'.join(f'  {name.ljust(width)}  {helps[name]}' for name in names)
                                 for title, names in GROUPS)
    result.epilog += ('\n\nRun evlet COMMAND --help for its options. A command that takes an instance accepts its name '
                      'either way: evlet dev acme or evlet dev --instance acme.')
    commands.help = 'One of the commands below'
    commands._choices_actions.clear()


def command_name(values):
    """The command word of an argument list: the first value that is not an option (or --project's path)."""
    values = iter(values)
    for value in values:
        if value == '--project':
            next(values, None)
        elif not value.startswith('-'):
            return value
    return None



def main(argv=None):
    values = list(sys.argv[1:] if argv is None else argv)
    command_parser = parser()
    if command_name(values) == 'journal':
        command_parser.error("'evlet journal' was renamed: use 'evlet activity verify INSTANCE'")
    args = command_parser.parse_args(move_instance_option(values))
    given = getattr(args, 'instance_arg', None)
    if given is not None:
        if getattr(args, 'instance', None) not in (None, given):
            command_parser.error(f'Two instances given: {given} and --instance {args.instance}')
        args.instance = given
    from . import connector
    if args.command in connector.COMMANDS:
        return connector.run(args)
    try:
        if getattr(args, 'port', None) is not None and not 1 <= args.port <= 65535:
            raise ValueError('Use a port between 1 and 65535')
        root = None
        if args.command not in WITHOUT_PROJECT:
            root = project_root(args.project)
            if args.command != 'project':  # evlet project prints the id itself, and refuses a broken project.json
                from .project import ensure_project
                try:
                    record, made = ensure_project(root)
                except ValueError as exc:
                    print(f'Note: {exc}', file=sys.stderr, flush=True)
                else:
                    if made:
                        print(f'Project id: {record["id"]}', file=sys.stderr, flush=True)
            if argv is None:
                from .setup import delegate
                delegate(root, values)
        _, run = command_entry(args.command)
        run(args, Context(root=root, values=values, console=argv is None, confirm=confirm))
    except (KeyboardInterrupt, BrokenPipeError):
        return
    except EOFError:
        print('Input ended before the command finished. Nothing was changed.', file=sys.stderr)
        raise SystemExit(1) from None
    except (ValueError, TypeError, SyntaxError, OSError, RuntimeError, ImportError, sqlite3.Error, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from None
