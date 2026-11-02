"""Write the finite-v1 plans and Python-evaluated scenarios consumed by the browser tests."""
from pathlib import Path
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tests'))
from finite_fixtures import collect

PATH = Path(__file__).resolve().parents[1] / 'frontend/src/evlet/tests/finite-fixtures.json'


def main():
    PATH.write_text(json.dumps(collect(), indent=1, sort_keys=True) + '\n')


if __name__ == '__main__': main()
