"""evlet effects check INSTANCE: recompute the rows records post to other models and report differences."""

from ..instances import activate_generation, load_runtime
from .project import child, instance_guard


def check_instance(root, name):
    """The differences of one instance (see evlet.effects.check); nothing is written.

    The instance lock is shared, like a running server's, so the check may run while the instance serves.
    """
    from ..effects import check
    path = child(root, 'instances', name)
    with instance_guard(path, serving=True):
        app = load_runtime(root, name)
        try:
            with activate_generation(app):
                app.initialize()
                return check(app)
        finally:
            app.database.close()
