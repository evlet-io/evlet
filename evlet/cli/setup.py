"""Create only the project containers."""

from pathlib import Path


def setup(directory):
    target = Path(directory).absolute()
    target.mkdir()
    try:
        (target / 'apps').mkdir()
        (target / 'instances').mkdir()
    except BaseException:
        for name in ('instances', 'apps'):
            folder = target / name
            if folder.is_dir():
                folder.rmdir()
        target.rmdir()
        raise
    return target
