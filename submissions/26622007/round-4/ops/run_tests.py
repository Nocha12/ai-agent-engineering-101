"""Runs every offline test of this shop in one go: ops/test_*.py and ops/deploy/test_*.py, each in its own
python -I process (test_tools_src also runs test_refund_rpc_ua). Run it from a full checkout (git worktree), not
from a copy of ops/ only: several tests read ../agent.json, ../notes.md and ../../../../scripts/round4.
~/ax-round4-venv/bin/python -I ops/run_tests.py   (pytest is needed for test_price_ceiling*)"""
import glob
import os
import subprocess
import sys

TIMEOUT = 600
HERE = os.path.dirname(os.path.abspath(__file__))
ROUND = os.path.dirname(HERE)
NEEDS = [os.path.join(ROUND, 'agent.json'), os.path.join(ROUND, 'notes.md'),
         os.path.join(ROUND, '..', '..', '..', 'scripts', 'round4', 'agent_kit.py')]


def main():
    missing = [os.path.relpath(p, ROUND) for p in NEEDS if not os.path.exists(p)]
    if missing:
        print('not a full checkout, missing:', ', '.join(missing), '- use git worktree add, not a copy of ops/')
        return 2
    try:
        import pytest  # noqa: F401
    except ImportError:
        print('pytest is missing in', sys.executable, '- uv pip install --python', sys.executable, 'pytest')
        return 2
    tests = sorted(glob.glob(os.path.join(HERE, 'test_*.py'))) + sorted(glob.glob(os.path.join(HERE, 'deploy', 'test_*.py')))
    bad = []
    for t in tests:
        name = os.path.relpath(t, ROUND)
        try:                                            # one stuck test does not hold the rest (review A M2)
            p = subprocess.run([sys.executable, '-I', '-B', t], cwd=ROUND, stdin=subprocess.DEVNULL, timeout=TIMEOUT,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        except subprocess.TimeoutExpired as e:
            p = subprocess.CompletedProcess(e.cmd, 124, (e.stdout or b'').decode(errors='replace') if isinstance(e.stdout, bytes)
                                            else (e.stdout or '') + f'\ntimed out after {TIMEOUT}s')
        print(('PASS ' if p.returncode == 0 else 'FAIL ') + name)
        if p.returncode:
            bad.append(name)
            print('\n'.join(p.stdout.splitlines()[-15:]))
    print('tests', len(tests), 'fail', len(bad))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
