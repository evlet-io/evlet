"""evlet project: the project id, its mode and its instances with their ids."""

import sqlite3


def add_project(commands):
    project = commands.add_parser('project', help='Show the project id, mode and instances, or give a fork its own id')
    actions = project.add_subparsers(dest='project_command', metavar='ACTION')
    actions.add_parser('new-id', help='Give this project a new id, such as for a fork of a cloned project')
    mode = actions.add_parser('mode', help='Set the mode recorded in project.json')
    mode.add_argument('mode', choices=('development', 'production'))


def instance_lines(root):
    """One line per instance: its name and id, or why it has none."""
    from ...instances import instance_identity
    paths = [path for path in sorted((root / 'instances').iterdir()) if path.is_dir() and not path.name.startswith('.')]
    found = {}
    for path in paths:
        try:
            found[path.name] = instance_identity(path)
        except (OSError, ValueError, sqlite3.Error) as exc:
            found[path.name] = exc
    lines = []
    for name, identity in found.items():
        if isinstance(identity, Exception):
            text = f'unreadable: {identity}'
        elif identity is None:
            text = 'no id yet (install an app, or run evlet migrate ' + name + ')'
        else:
            text = identity['id']
            twins = [other for other, item in found.items() if other != name and isinstance(item, dict) and item['id'] == identity['id']]
            if twins and identity['name'] != name:
                text += f' (the same as {", ".join(twins)}: a copied folder; run evlet instance new-id {name})'
        lines.append((name, text))
    return lines


def run_project(args, context):
    from ..project import ensure_project, new_project_id, set_project_mode
    root = context.root
    if args.project_command == 'new-id':
        old, record = new_project_id(root)
        print(f'Project id: {record["id"]}' + (f' (was {old})' if old else ''))
        print('Commit project.json. Instances keep their own ids.')
        return
    if args.project_command == 'mode':
        changed = set_project_mode(root, args.mode)
        print(f'Mode: {args.mode}' + ('' if changed else ' (unchanged)'))
        return
    record, _ = ensure_project(root)
    if record is None:
        raise ValueError(f'{root / "project.json"} could not be written; run evlet project as the owner of the project folder')
    print(f'Project id: {record["id"]}')
    print(f'Created: {record["created"]}')
    print(f'Mode: {record["mode"]}')
    print('Endpoint: none')
    lines = instance_lines(root)
    print('Instances:' + ('' if lines else ' none'))
    width = max((len(name) for name, _ in lines), default=0)
    for name, text in lines:
        print(f'  {name.ljust(width)}  {text}')


COMMANDS = {'project': (add_project, run_project)}
