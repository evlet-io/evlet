"""evlet sites: the public sites an instance keeps itself (evlet.sites, docs/sites.md).

Each command opens the instance like evlet host --live does, so a running server serves a publish or a
rollback within a second, without a restart.
"""

import argparse
from pathlib import Path
import sys

from . import INSTANCE_HELP

ROUTING_HELP = 'app: every path without a file type gets index.html; pages: /about/ serves about/index.html'


def _settings(command, create):
    command.add_argument('--routing', choices=('app', 'pages'), default='app' if create else None, help=ROUTING_HELP)
    command.add_argument('--connect', action='append', metavar='ORIGIN',
                         help='An https origin the pages may connect to (CSP connect-src); repeat for more')
    command.add_argument('--api', action='append', metavar='APP.SCOPE', help='An API scope whose public endpoints the site may call')
    command.add_argument('--max-file-bytes', type=int, help='Largest file (default 10 MiB)')
    command.add_argument('--max-site-bytes', type=int, help='All draft files together (default 100 MiB)')
    command.add_argument('--max-files', type=int, help='Number of draft files (default 5000)')
    if not create:
        command.add_argument('--no-connect', action='store_true', help='Remove every CSP origin')
        command.add_argument('--no-api', action='store_true', help='Remove every API scope')


def add_sites(commands):
    sites = commands.add_parser('sites', help='Public sites the instance keeps itself: draft files, publish and roll back')
    verbs = sites.add_subparsers(dest='sites_command', required=True)
    made = {
        'list': verbs.add_parser('list', help='Print the sites with their current release and hosts'),
        'create': verbs.add_parser('create', help='Add a site with an empty draft'),
        'set': verbs.add_parser('set', help="Change a site's routing, CSP origins, API scopes or limits"),
        'delete': verbs.add_parser('delete', help='Remove a site, its draft and its releases'),
        'files': verbs.add_parser('files', help='Print the draft files'),
        'put': verbs.add_parser('put', help='Write a file or a folder of files into the draft'),
        'rm': verbs.add_parser('rm', help='Remove a draft file'),
        'publish': verbs.add_parser('publish', help='Publish the draft as a new release and serve it'),
        'rollback': verbs.add_parser('rollback', help='Serve an earlier release again'),
        'releases': verbs.add_parser('releases', help='Print the releases, newest first'),
    }
    for verb, command in made.items():
        command.add_argument('instance', help=INSTANCE_HELP)
        command.add_argument('--project', default=argparse.SUPPRESS, help='Explicit project path')
        if verb != 'list':
            command.add_argument('site', help='Site name')
    _settings(made['create'], True)
    _settings(made['set'], False)
    made['delete'].add_argument('--yes', action='store_true', help='Delete without asking')
    made['put'].add_argument('source', help='A file, or a folder whose files keep their relative paths')
    made['put'].add_argument('--as', dest='target', metavar='PATH', help='The draft path of a single file, or the folder to put a folder under')
    made['put'].add_argument('--replace', action='store_true', help='With a folder: also remove draft files the folder does not have')
    made['rm'].add_argument('path', help='Draft path, such as about/index.html')
    made['publish'].add_argument('--note', default='', help='A short note kept with the release')
    made['rollback'].add_argument('release', nargs='?', help='The release to serve (default: the one before the current release)')


def _table(rows):
    widths = [max(len(str(row[index])) for row in rows) + 3 for index in range(len(rows[0]) - 1)]
    return '\n'.join(''.join(str(value).ljust(width) for value, width in zip(row, widths)) + str(row[-1]) for row in rows)


def _size(value):
    for unit in ('bytes', 'KiB', 'MiB', 'GiB'):
        if value < 1024 or unit == 'GiB':
            return f'{value} {unit}' if unit == 'bytes' else f'{value:.1f} {unit}'
        value /= 1024


def _local_files(source, target):
    """{draft path: bytes} of a local file or folder. Dotfiles and dot folders are left out; links are refused."""
    source = Path(source)
    if source.is_symlink():
        raise ValueError(f'{source} is a symbolic link')
    if source.is_file():
        return {target or source.name: source.read_bytes()}, []
    if not source.is_dir():
        raise ValueError(f'{source} is not a file or a folder')
    prefix = (target.strip('/') + '/') if target else ''
    found, skipped = {}, []
    for item in sorted(source.rglob('*')):
        relative = item.relative_to(source)
        if any(part.startswith('.') for part in relative.parts):
            skipped.append(relative.as_posix())
            continue
        if item.is_symlink():
            raise ValueError(f'{item} is a symbolic link; a site holds regular files only')
        if item.is_file():
            found[prefix + relative.as_posix()] = item.read_bytes()
    if not found:
        raise ValueError(f'{source} holds no files to put')
    return found, sorted({path.split('/')[0] for path in skipped})


def run_sites(args, context):
    from ... import sites
    from ...instances import load_runtime
    app = load_runtime(context.root, args.instance)
    business, name, verb = app.business, args.instance, args.sites_command
    try:
        if verb == 'list':
            found = sites.list_sites(business)
            if not found:
                print(f'Instance {name} has no sites. Add one with evlet sites create {name} SITE')
                return
            rows = [('SITE', 'ROUTING', 'RELEASE', 'SERVED ON')]
            rows += [(site['name'], site['routing'], site['current_release'] or '-', ', '.join(site['served_on']) or
                      ('pending: ' + ', '.join(site['hosts']) if site['hosts'] else '-')) for site in found]
            print(_table(rows))
        elif verb in ('create', 'set'):
            options = {'routing': args.routing, 'max_file_bytes': args.max_file_bytes, 'max_site_bytes': args.max_site_bytes,
                       'max_files': args.max_files}
            if verb == 'create':
                site = sites.create_site(business, args.site, actor='cli', connect=args.connect or (), api=args.api or (), **options)
                print(f'Created site {args.site}. Put its files with evlet sites put {name} {args.site} FOLDER, publish them with '
                      f'evlet sites publish {name} {args.site}, and serve it on a host with evlet host add {name} HOST --root {args.site} --live')
            else:
                connect = () if args.no_connect else args.connect
                api = () if args.no_api else args.api
                site = sites.update_site(business, args.site, actor='cli', connect=connect, api=api, **options)
                print(f'Site {args.site}: routing {site["routing"]}, CSP origins {" ".join(site["connect"]) or "none"}, '
                      f'API scopes {" ".join(site["api"]) or "none"}')
        elif verb == 'delete':
            if not args.yes and not context.confirm(f'Delete site {args.site} with its draft and every release?'):
                raise ValueError('Not deleted. Run again with --yes to delete without asking')
            sites.delete_site(business, args.site, actor='cli')
            print(f'Deleted site {args.site}')
        elif verb == 'files':
            files = sites.list_files(business, args.site)
            if not files:
                print(f'The draft of {args.site} is empty')
                return
            print(_table([('PATH', 'SIZE')] + [(item['path'], _size(item['size'])) for item in files]))
        elif verb == 'put':
            found, skipped = _local_files(args.source, args.target)
            written = sites.write_files(business, args.site, found, actor='cli')
            removed = []
            if args.replace and Path(args.source).is_dir():
                prefix = (args.target.strip('/') + '/') if args.target else ''
                for item in sites.list_files(business, args.site):
                    if item['path'].startswith(prefix) and item['path'] not in found:
                        sites.delete_file(business, args.site, item['path'], actor='cli')
                        removed.append(item['path'])
            print(f'Wrote {len(written)} file(s) to the draft of {args.site}' + (f', removed {len(removed)}' if removed else '')
                  + f'. Publish with evlet sites publish {name} {args.site}')
            if skipped:
                print('Left out dotfiles: ' + ', '.join(skipped), file=sys.stderr)
        elif verb == 'rm':
            sites.delete_file(business, args.site, args.path, actor='cli')
            print(f'Removed {args.path} from the draft of {args.site}')
        elif verb == 'publish':
            release = sites.publish(business, args.site, actor='cli', note=args.note)
            print(f'Published {args.site} release {release["release"]}: {release["files"]} file(s), {_size(release["bytes"])}. '
                  'A running instance serves it within a second.')
        elif verb == 'rollback':
            release = sites.rollback(business, args.site, args.release, actor='cli')
            print(f'Site {args.site} serves release {release["release"]} again')
        elif verb == 'releases':
            found = sites.list_releases(business, args.site)
            if not found:
                print(f'Site {args.site} has no release yet')
                return
            print(_table([('RELEASE', 'FILES', 'SIZE', 'BY', 'NOTE')] + [
                (item['release'] + (' (current)' if item['current'] else ''), item['files'], _size(item['bytes'] or 0),
                 item['published_by'] or '-', item['note'] or '') for item in found]))
    finally:
        app.database.close()


COMMANDS = {'sites': (add_sites, run_sites)}
