"""evlet ledger list --instance NAME: each ledger model with its table, row count and options."""

from ..instances import activate_generation, load_runtime
from .project import child, instance_guard


def list_instance(root, name):
    """describe() rows for one instance (see evlet.ledger.describe); nothing is written.

    The instance lock is shared, like a running server's, so the listing may run while the instance serves.
    """
    from ..ledger import describe
    path = child(root, 'instances', name)
    with instance_guard(path, serving=True):
        app = load_runtime(root, name)
        try:
            with activate_generation(app):
                app.initialize()
                return describe(app.database)
        finally:
            app.database.close()
