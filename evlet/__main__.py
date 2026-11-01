import os
import sys

# `python -m` puts the working directory first on sys.path. Inside an app folder
# that would let unhashed sibling files shadow imports, bypassing apps.lock.
if sys.path and not sys.flags.safe_path and sys.path[0] in ('', os.getcwd()):
    del sys.path[0]

from .cli import main  # noqa: E402

if __name__ == '__main__':
    main()
