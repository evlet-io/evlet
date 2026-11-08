"""evlet jobs list --instance NAME: the newest background jobs with their state and progress."""

from ..instances import activate_generation, load_runtime
from .project import child, instance_guard


def list_instance(root, name):
    """JobQueue.recent() rows for one instance; nothing is written beyond the queue's own tables.

    The instance lock is shared, like a running server's, so the listing may run while the instance serves.
    """
    path = child(root, 'instances', name)
    with instance_guard(path, serving=True):
        app = load_runtime(root, name)
        try:
            with activate_generation(app):
                app.initialize()
                jobs = app.business.jobs if app.business is not None else None
                return [] if jobs is None else jobs.recent()
        finally:
            app.database.close()
