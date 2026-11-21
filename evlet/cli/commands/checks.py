"""evlet ids, evlet activity, evlet ledger and evlet jobs: read-only reviews of an instance."""

import argparse
import sys

from ...identity import text as text_id
from . import INSTANCE_HELP, add_instance_option, choose_instance


def add_ids(commands):
    ids = commands.add_parser('ids', help='Check record ids')
    ids_commands = ids.add_subparsers(dest='ids_command', required=True)
    ids_check = ids_commands.add_parser('check', help='Report rows with an impossible id and tables that allow id changes')
    ids_check.add_argument('instance', help=INSTANCE_HELP)
    ids_check.add_argument('--project', default=argparse.SUPPRESS, help='Explicit project path')


def run_ids(args, context):
    from ..ids import check_instance
    problems = check_instance(context.root, args.instance)
    for line in problems:
        print(line)
    if problems:
        print(f'{len(problems)} problem' + ('' if len(problems) == 1 else 's'), file=sys.stderr)
        raise SystemExit(1)
    print('Every model row has a record id, and the database refuses id changes')


def add_activity(commands):
    activity = commands.add_parser('activity', help="Check records' Activity")
    activity_commands = activity.add_subparsers(dest='activity_command', required=True)
    verify = activity_commands.add_parser('verify', help='Report breaks in the hash chains of tamper-evident Activity')
    verify.add_argument('instance', help=INSTANCE_HELP)
    verify.add_argument('--project', default=argparse.SUPPRESS, help='Explicit project path')


def run_activity(args, context):
    from ..activity import verify_instance
    checked, breaks = verify_instance(context.root, args.instance)
    for entry, model, record_id, message in breaks:
        print(f'Entry {entry} ({model} {text_id(record_id)}): {message}')
    if breaks:
        print(f'{len(breaks)} break{"s" if len(breaks) > 1 else ""} in {checked} hashed entries', file=sys.stderr)
        raise SystemExit(1)
    print(f'Activity verified: {checked} hashed entries, no breaks')


def add_ledger(commands):
    ledger = commands.add_parser('ledger', help='Review ledger models')
    ledger_commands = ledger.add_subparsers(dest='ledger_command', required=True)
    ledger_list = ledger_commands.add_parser('list', help='Print each ledger model with its table, row count and options')
    add_instance_option(ledger_list)
    ledger_list.add_argument('--project', default=argparse.SUPPRESS, help='Explicit project path')


def run_ledger(args, context):
    from ..ledger import list_instance
    from ...ledger import table
    print(table(list_instance(context.root, args.instance or choose_instance(context.root, 'ledger list'))))


def add_jobs(commands):
    jobs = commands.add_parser('jobs', help='Review background jobs')
    jobs_commands = jobs.add_subparsers(dest='jobs_command', required=True)
    jobs_list = jobs_commands.add_parser('list', help='Print the newest jobs with their state, attempts and progress')
    add_instance_option(jobs_list)
    jobs_list.add_argument('--project', default=argparse.SUPPRESS, help='Explicit project path')


def run_jobs(args, context):
    from ..jobs import list_instance
    from ...jobs import table
    print(table(list_instance(context.root, args.instance or choose_instance(context.root, 'jobs list'))))


COMMANDS = {
    'ids': (add_ids, run_ids),
    'activity': (add_activity, run_activity),
    'ledger': (add_ledger, run_ledger),
    'jobs': (add_jobs, run_jobs),
}
