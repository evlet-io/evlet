"""Check editable, wheel and sdist imports outside a generated app checkout."""
from pathlib import Path
import argparse
import os
import subprocess
import sys
import tempfile
import venv

from evlet.cli.app import create_app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('framework_wheel', type=Path)
    args = parser.parse_args()
    wheel = args.framework_wheel.resolve()
    with tempfile.TemporaryDirectory(prefix='evlet-flat-package-') as directory:
        root = Path(directory)
        (root / 'project/apps').mkdir(parents=True)
        app = create_app(root / 'project', 'flatcheck')
        subprocess.run([sys.executable, '-m', 'build', '--outdir', str(root / 'dist'), str(app)], check=True, stdout=subprocess.DEVNULL)
        venv.EnvBuilder(with_pip=True).create(root / 'env')
        python = root / 'env' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        subprocess.run([str(python), '-m', 'pip', 'install', '--upgrade', '--require-hashes', '-r', str(Path(__file__).resolve().parents[1] / 'requirements-bootstrap.lock')], check=True, stdout=subprocess.DEVNULL)
        subprocess.run([str(python), '-m', 'pip', 'install', '--disable-pip-version-check', str(wheel), str(next((root / 'dist').glob('flatcheck-*.whl')))], check=True, stdout=subprocess.DEVNULL)
        probe = 'import flatcheck; from flatcheck.models.item import FlatcheckItem; assert FlatcheckItem.__module__ == "flatcheck.models.item"; print(flatcheck.__file__)'
        subprocess.run([str(python), '-I', '-c', probe], cwd=root, check=True)
        subprocess.run([str(python), '-m', 'pip', 'install', '--disable-pip-version-check', '--no-deps', '--force-reinstall', str(next((root / 'dist').glob('flatcheck-*.tar.gz')))], check=True, stdout=subprocess.DEVNULL)
        subprocess.run([str(python), '-I', '-c', probe], cwd=root, check=True)
        subprocess.run([str(python), '-m', 'pip', 'install', '--disable-pip-version-check', '--no-deps', '-e', str(app)], check=True, stdout=subprocess.DEVNULL)
        subprocess.run([str(python), '-I', '-c', probe], cwd=root, check=True)
    print('Flat app wheel, source distribution and editable imports passed outside the checkout')


if __name__ == '__main__': main()
