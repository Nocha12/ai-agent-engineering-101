"""Local, pinned four-file quote deployment preparation. Never runs buyer code."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess


PREFIX = 'submissions/26622007/week-08/'
FILES = tuple(PREFIX + name for name in (
    'buyer_quote_alignment_policy.py', 'buyer_quote_alignment.py',
    'tools/quote_alignment_measure.py', 'market_buyer.py'))


def git(repo, *args):
    return subprocess.check_output(
        ['git', '-C', str(repo), *args], stderr=subprocess.PIPE, timeout=15)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def repository(raw):
    path = Path(raw).absolute()
    if path.resolve() != path:
        raise ValueError('repository symlink or noncanonical path')
    if Path(git(path, 'rev-parse', '--show-toplevel').decode().strip()) != path:
        raise ValueError('repository root required')
    return path


def revision(value):
    if not re.fullmatch(r'[0-9a-f]{40}', value):
        raise ValueError('full commit hash required')
    return value


def prepare(args):
    source, target = repository(args.source), repository(args.target)
    commit, head = revision(args.commit), revision(args.expected_head)
    if git(source, 'rev-parse', commit + '^{commit}').decode().strip() != commit:
        raise ValueError('source commit mismatch')
    if git(target, 'rev-parse', 'HEAD').decode().strip() != head:
        raise ValueError('target HEAD changed')
    stage = Path(args.staging).absolute()
    if stage.resolve() != stage or stage.is_relative_to(target) or stage.is_relative_to(source):
        raise ValueError('staging must be separate and nonsymlinked')
    blobs = {}
    for name in FILES:
        entry = git(source, 'ls-tree', commit, '--', name).decode().split()
        if len(entry) != 4 or entry[0] != '100644' or entry[1] != 'blob':
            raise ValueError('source must contain regular allowlisted files')
        blobs[name] = git(source, 'show', commit + ':' + name)
    stage.mkdir(mode=0o700)
    for name, data in blobs.items():
        path = stage / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    manifest = dict(version=1, commit=commit, expected_head=head,
                    target=str(target), files={n: digest(b) for n, b in blobs.items()})
    data = (json.dumps(manifest, sort_keys=True, indent=2) + '\n').encode()
    (stage / 'manifest.json').write_bytes(data)
    return dict(status='prepared', manifest_sha256=digest(data))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    p = commands.add_parser('prepare')
    for name in ('source', 'commit', 'target', 'expected-head', 'staging'):
        p.add_argument('--' + name, required=True)
    args = parser.parse_args()
    try:
        result = prepare(args)
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print(json.dumps(dict(status='rejected', error=str(exc))))
        return 2
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
