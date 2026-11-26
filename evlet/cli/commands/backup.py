"""evlet backup and evlet restore: copy an instance's database files as one set, and put a set back."""

import sys

from . import add_instance_option, choose_instance


def add_backup(commands):
    backup = commands.add_parser('backup', help="Copy an instance's four database files into data/backups as one set")
    add_instance_option(backup)
    backup.add_argument('--to', help='Write the set into this directory instead of data/backups')
    backup.add_argument('--with-files', action='store_true', help="Also archive the instance's files/ folder (uploaded attachments) with its sites/")


def run_backup(args, context):
    from ..backup import backup
    root = context.root
    name = args.instance or choose_instance(root, 'backup')
    written = backup(root, name, args.to, with_files=args.with_files)
    print('Backup: ' + str(written[0]))
    print('With: ' + ', '.join(item.name for item in written[1:]))
    instance_path = root / 'instances' / name
    if not args.with_files:
        print(f'Not included: {instance_path / "files"} (uploaded attachments); add --with-files to archive it.')
    if written[-1].name.endswith(('-files.tar.gz', '-sites.tar.gz')) and (instance_path / 'sites').is_dir():
        print(f'Sites: {instance_path / "sites"} is in {written[-1].name}; evlet restore leaves the sites folder as it is.')
    print(f'Not included: {instance_path / "secret.key"}. Without it a restored database cannot open saved connector, '
          'mailbox and two-step secrets. Keep a copy stored apart from these backups.')


def add_restore(commands):
    restore = commands.add_parser('restore', help="Replace an instance's database files with a backup set, with the server stopped")
    restore.add_argument('backup', help='The main file of the set, such as data/backups/20261112T101500000000Z.sqlite3')
    add_instance_option(restore)
    restore.add_argument('--yes', metavar='NAME', help='Restore without asking; give the instance name to confirm')


def run_restore(args, context):
    from ..backup import restore
    root = context.root
    name = args.instance or choose_instance(root, 'restore')
    if args.yes is not None:
        if args.yes != name:
            raise ValueError(f'--yes must name the instance being restored: {name}')
    elif not sys.stdin.isatty() or input(f'This replaces every database file of {name}. Type the instance name to restore: ').strip() != name:
        raise ValueError('Restore cancelled; nothing was changed')
    safety, single = restore(root, name, args.backup)
    print(f'Instance {name}: restored from {args.backup}')
    if safety:
        print(f'The files it replaced are kept in {safety}')
    if single:
        print(f'This backup is from before sessions, logs and Eva had their own files. Run evlet migrate {name} to move them before serving.')


COMMANDS = {'backup': (add_backup, run_backup), 'restore': (add_restore, run_restore)}
