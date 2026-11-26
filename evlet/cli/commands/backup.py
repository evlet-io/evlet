"""evlet backup and evlet restore: copy an instance's database files as one set, and put a set back."""

import sys

from . import add_instance_option, choose_instance


def add_backup(commands):
    backup = commands.add_parser('backup', help="Copy an instance's four database files into data/backups as one set")
    add_instance_option(backup)
    backup.add_argument('--to', help='Write the set into this directory instead of data/backups')
    backup.add_argument('--with-files', action='store_true', help="Also archive the instance's files/ folder (uploaded attachments)")


def run_backup(args, context):
    from ..core import backup_create
    name = args.instance or choose_instance(context.root, 'backup')
    backup_create(context.root, name, to=args.to, with_files=args.with_files, out=print)


def add_restore(commands):
    restore = commands.add_parser('restore', help="Replace an instance's database files with a backup set, with the server stopped")
    restore.add_argument('backup', help='The main file of the set, such as data/backups/20261112T101500000000Z.sqlite3')
    add_instance_option(restore)
    restore.add_argument('--yes', metavar='NAME', help='Restore without asking; give the instance name to confirm')


def run_restore(args, context):
    from ..core import backup_restore
    name = args.instance or choose_instance(context.root, 'restore')
    if args.yes is not None and args.yes != name:
        raise ValueError(f'--yes must name the instance being restored: {name}')
    # Without --yes NAME, the instance name must be typed in a terminal.
    confirm = (lambda question: input(question).strip() == name) if sys.stdin.isatty() else None
    backup_restore(context.root, name, args.backup, yes=args.yes is not None, confirm=confirm, out=print)


COMMANDS = {'backup': (add_backup, run_backup), 'restore': (add_restore, run_restore)}
