"""evlet restart and evlet upgrade: restart a served instance, or move it to a newer Evlet."""

from . import INSTANCE_HELP


def add_restart(commands):
    restart = commands.add_parser('restart', help="Check an instance, then restart its systemd service and wait until it answers")
    restart.add_argument('instance', help=INSTANCE_HELP)
    restart.add_argument('--check', action='store_true', help='Run the checks only; restart nothing')
    restart.add_argument('--yes', action='store_true', help='Restart without asking')
    restart.add_argument('--service', metavar='NAME', help='The systemd unit to restart (default: evlet-<instance>)')
    restart.add_argument('--timeout', type=float, default=30.0, help='Seconds to wait for /health after the restart (default: 30)')


def yes_no():
    """A [y/N] question in a terminal; None without one, so the command says how to go on without asking."""
    from ..restart import yes_no
    return yes_no(None, None)


def run_restart(args, context):
    from ..core import restart_instance
    from ..endpoint import shell_operation
    if not args.check and not args.service:
        # A project endpoint that runs the instances restarts them itself; systemd units are not used then.
        done = shell_operation(context.root, '/_/project/restart', {'instance': args.instance})
        if done is not None:
            if not done:
                raise SystemExit(1)
            return
    confirm = None if args.yes or args.check else yes_no()
    if not restart_instance(context.root, args.instance, service=args.service, check_only=args.check, yes=args.yes,
                            confirm=confirm, timeout=args.timeout, capture=False, out=print).ok:
        raise SystemExit(1)


def add_upgrade(commands):
    upgrade = commands.add_parser('upgrade', help="Stage a newer Evlet, then plan, stop, back up, migrate and restart its instances")
    upgrade.add_argument('instance', help=INSTANCE_HELP)
    upgrade.add_argument('--to', metavar='VERSION', help='Install this version (default: the newest 0.x.* of the current minor, or of the current major from 1.0)')
    upgrade.add_argument('--yes', action='store_true', help='Upgrade without asking')
    upgrade.add_argument('--all-instances', action='store_true', help='Upgrade every installed instance sharing the project environment')
    upgrade.add_argument('--service', metavar='NAME', help='The systemd unit to stop and restart (default: evlet-<instance>)')


def run_upgrade(args, context):
    from ..core import upgrade_evlet
    from ..endpoint import exists, running, shell_operation
    if not args.service and exists(context.root) and running(context.root):
        # The endpoint runs every instance: it stops, backs up, migrates and starts them all, then restarts itself.
        if not args.yes and not (yes_no() or (lambda question: False))('The project endpoint upgrades Evlet for every instance it runs. Go on?'):
            raise SystemExit('Not upgraded. Pass --yes to upgrade without asking.')
        if not shell_operation(context.root, '/_/project/upgrade', {'to': args.to or ''}):
            raise SystemExit(1)
        return
    if not upgrade_evlet(context.root, args.instance, to=args.to, service=args.service, yes=args.yes,
                         confirm=None if args.yes else yes_no(), all_instances=args.all_instances, capture=False, out=print).ok:
        raise SystemExit(1)


COMMANDS = {'restart': (add_restart, run_restart), 'upgrade': (add_upgrade, run_upgrade)}
