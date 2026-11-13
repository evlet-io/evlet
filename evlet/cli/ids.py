"""evlet ids check INSTANCE: report model rows whose id no record can have, and tables that allow id changes."""

from ..instances import activate_generation, load_runtime
from .project import child, instance_guard


def check_instance(root, name):
    """Problem lines for one instance (see evlet.identity.check_ids); nothing is written.

    The instance lock is shared, like a running server's, so the check may run while the instance serves.
    It runs before `evlet migrate` too, so it reports the missing id guard instead of refusing to start.
    """
    from ..identity import check_ids
    path = child(root, 'instances', name)
    with instance_guard(path, serving=True):
        app = load_runtime(root, name)
        try:
            with activate_generation(app), app.database.transaction() as tx:
                return check_ids(tx.connection, app.database.registry)
        finally:
            app.database.close()
