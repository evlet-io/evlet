"""Measure the public facade in fresh processes, without creating application data.

Run on an otherwise quiet host; startup timing is descriptive, never a CI pass/fail threshold.
"""
import argparse
import json
from pathlib import Path
import statistics
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--samples', type=int, default=7)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if not 1 <= args.samples <= 50:
        parser.error('Use from1 to50 samples')
    cases = {'models': 'from evlet import Model, Text', 'all_exports': 'from evlet import *',
             'cli': 'from evlet.cli.main import parser; parser()'}
    result = {'python': sys.version, 'notes': ['Fresh child processes, warm filesystem cache; shared-host results vary.',
                                             'RSS is total child-process high water, not incremental retained heap.'], 'cases': {}}
    for name, statement in cases.items():
        code = ('import json,resource,sys,time; started=time.perf_counter(); ' + statement +
                '; print(json.dumps({"seconds":time.perf_counter()-started,"rss_kib":resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,'
                '"evlet_modules":sum(key.startswith("evlet.") for key in sys.modules)}))')
        rows = [json.loads(subprocess.check_output([sys.executable, '-c', code], text=True, timeout=30)) for _ in range(args.samples)]
        result['cases'][name] = {'samples': rows, 'median_seconds': statistics.median(row['seconds'] for row in rows),
                                'median_rss_kib': statistics.median(row['rss_kib'] for row in rows)}
    text = json.dumps(result, indent=2) + '\n'
    if args.output:
        args.output.write_text(text)
    print(text, end='')


if __name__ == '__main__':
    main()
