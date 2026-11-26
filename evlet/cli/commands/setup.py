"""evlet setup: create a project, and optionally its apps, an instance and a dev server or a public host."""

from pathlib import Path
import sys


def check_framework_source(framework):
    """Refuse, in one line, to set up a project from an Evlet source checkout whose browser UI is not built yet;
    pip would fail with a long build traceback that ends in the same advice."""
    if framework is None:
        source = Path(__file__).resolve().parents[3]
    elif Path(framework).is_dir():
        source = Path(framework)
    else:
        return  # A wheel, an archive or a requirement: pip reports its own problems.
    if (source / 'pyproject.toml').is_file() and (source / 'frontend/package.json').is_file() \
            and not (source / 'evlet/ui/static/index.html').is_file():
        raise ValueError(f'The Evlet source checkout at {source} has no built browser UI, so it cannot be installed into a project. '
                         f'Build it first: npm --prefix {source / "frontend"} ci && npm --prefix {source / "frontend"} run build')


def add_setup(commands):
    setup = commands.add_parser('setup', help='Create apps/, instances/ and an isolated .venv')
    setup.add_argument('directory')
    setup.add_argument('--framework', help='Reviewed local framework wheel or source for offline/development setup')
    setup.add_argument('--app', action='append', metavar='NAMES', help='Create these new apps; a comma list such as notes,crm')
    setup.add_argument('--get', action='append', metavar='GIT_URLS', help='Fetch these apps from Git like evlet app --get; a comma list')
    setup.add_argument('--instance', metavar='NAME', help='Create this instance and install the apps in it')
    setup.add_argument('--trust', action='store_true', help='Trust the fetched and local app code for this whole run instead of asking')
    mode = setup.add_mutually_exclusive_group()
    mode.add_argument('--dev', action='store_true', help='Then run evlet dev on loopback and print a sign-in link')
    mode.add_argument('--host', metavar='HOSTNAME', help='Then prepare a Caddy site and a systemd unit for this public hostname')
    setup.add_argument('--port', type=int, default=8000, help='Port evlet dev or the service listens on (default: 8000)')
    setup.add_argument('--ip', action='append', help="With --host: this server's public address, for an exact DNS match; optional, kept in deploy/server.json")
    setup.add_argument('--apply', action='store_true', help='With --host: run the printed sudo steps; each is shown first')
    setup.add_argument('--service-user', metavar='USER', help='With --host: run the service as this unprivileged account instead of you (also EVLET_SERVICE_USER)')


def run_setup(args, context):
    from ..setup import setup
    from .. import site
    directory = Path(args.project) / args.directory if args.project else args.directory
    check_framework_source(args.framework)
    if not site.wanted(args):
        created = setup(directory, framework=args.framework)
        print(f'Project created: {created}')
        print(site.project_line(created))
        print(f'Next: cd {created} && evlet app notes && evlet instance acme && evlet install notes --instance acme '
              '&& evlet dev --instance acme')
        return
    site.check(args)
    root = site.project(directory, framework=args.framework)
    if context.console:
        # Continue inside the project's own .venv; the re-run skips the project step.
        from ..setup import delegate
        sys.stdout.flush()  # exec discards unwritten output
        delegate(root, context.values)
    site.run(root, args, confirm=context.confirm)


COMMANDS = {'setup': (add_setup, run_setup)}
