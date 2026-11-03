"""evlet dev-link: a fresh one-time sign-in link for a running development server."""

from ..database import Database
from ..devlink import MINUTES, issue, link, running_playgrounds
from ..models import Registry
from .project import project_root


def message(username, url):
    return f'Sign in as {username} (works once, for {MINUTES} minutes):\n  {url}'


def playground_link(port, user):
    running = running_playgrounds()
    if port is not None:
        running = [item for item in running if item[0] == port]
    if not running:
        raise ValueError('No evlet playground is running' + (f' on port {port}' if port is not None else '') + '. Start one with evlet playground')
    if port is None and len({item[0] for item in running}) > 1:
        raise ValueError('Several Playgrounds are running. Choose one with --port ' + ' or --port '.join(str(item[0]) for item in running))
    found, directory = running[0]
    token, username = issue(Database(directory / 'playground.sqlite3', Registry(), must_exist=True), user)
    return message(username, link(f'http://127.0.0.1:{found}', token))


def instance_link(root, name, port, user):
    from ..instances import read_instance
    from .main import choose_instance
    from .project import child, instance_guard
    from .serve import declares_primary
    name = name or choose_instance(root, 'dev-link')
    path = child(root, 'instances', name)
    config = read_instance(path)
    # The same rule as evlet dev: links are for local instances that evlet dev can serve.
    if config.domain or declares_primary(path):
        raise ValueError('Sign-in links are for local instances served with evlet dev')
    if not (path / 'data/database.sqlite3').is_file():
        raise ValueError(f'Instance {name} has no database yet. Start it with evlet dev first')
    with instance_guard(path, serving=True):
        token, username = issue(Database(path / 'data/database.sqlite3', Registry(), must_exist=True), user)
    return message(username, link(f'http://127.0.0.1:{port or 8000}', token))


def dev_link(project=None, *, playground=False, instance=None, port=None, user=None):
    """The link text for the project's instance, or for the running Playground outside a project."""
    if not playground:
        try:
            root = project_root(project)
        except ValueError:
            if project:
                raise
        else:
            return instance_link(root, instance, port, user)
    return playground_link(port, user)
