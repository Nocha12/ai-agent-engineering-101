"""Local, pinned four-file quote deployment preparation. Never runs buyer code."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import re
import subprocess


PREFIX = 'submissions/26622007/week-08/'
FILES = tuple(PREFIX + name for name in (
    'buyer_quote_alignment_policy.py', 'buyer_quote_alignment.py',
    'tools/quote_alignment_measure.py', 'market_buyer.py'))
OFF = b"SOURCE_CONTROL = True\nENABLED = False\nSTART = 0\nSTUDY = ''\n"


def git(repo, *args):
    return subprocess.check_output(
        ['git', '--no-optional-locks', '-c', 'core.fsmonitor=false',
         '-C', str(repo), *args], stderr=subprocess.PIPE, timeout=15)


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


def safe_file(root, name):
    path = root / name
    if path.resolve() != path or not path.is_relative_to(root):
        raise ValueError('symlink or path escape')
    if path.exists() and not path.is_file():
        raise ValueError('regular file required')
    return path


def policy(data):
    if len(data) > 4096:
        raise ValueError('oversized policy')
    tree = ast.parse(data)
    values = {}
    for node in tree.body:
        if (not isinstance(node, ast.Assign) or len(node.targets) != 1
                or not isinstance(node.targets[0], ast.Name)):
            raise ValueError('four literal assignments required')
        name = node.targets[0].id
        if name in values:
            raise ValueError('duplicate policy key')
        values[name] = ast.literal_eval(node.value)
    if set(values) != {'SOURCE_CONTROL', 'ENABLED', 'START', 'STUDY'}:
        raise ValueError('four policy keys required')
    if any(type(values[n]) is not bool for n in ('SOURCE_CONTROL', 'ENABLED')):
        raise ValueError('policy flags must be bool')
    start, study = values['START'], values['STUDY']
    if type(start) is not int or start < 0:
        raise ValueError('START must be nonnegative int')
    if type(study) is not str or not study.isascii() or len(study) > 64:
        raise ValueError('STUDY must be ASCII up to 64 characters')
    if values['ENABLED'] and (not values['SOURCE_CONTROL'] or start == 0 or not study):
        raise ValueError('ON requires source control, positive START and STUDY')
    return values


def validate(args):
    target = repository(args.target)
    head = revision(args.expected_head)
    if git(target, 'rev-parse', 'HEAD').decode().strip() != head:
        raise ValueError('target HEAD changed')
    stage = Path(args.staging).absolute()
    if stage.resolve() != stage or stage.is_relative_to(target) or target.is_relative_to(stage):
        raise ValueError('staging must be separate and nonsymlinked')
    data = safe_file(stage, 'manifest.json').read_bytes()
    if digest(data) != args.manifest_sha256:
        raise ValueError('manifest hash mismatch')
    manifest = json.loads(data)
    if (set(manifest) != {'version', 'commit', 'expected_head', 'target', 'files'}
            or type(manifest['version']) is not int or manifest['version'] != 1
            or manifest['expected_head'] != head or manifest['target'] != str(target)
            or set(manifest['files']) != set(FILES)):
        raise ValueError('manifest contract mismatch')
    revision(manifest['commit'])
    blobs = {}
    for name in FILES:
        blobs[name] = safe_file(stage, name).read_bytes()
        if digest(blobs[name]) != manifest['files'][name]:
            raise ValueError('staged blob hash mismatch')
        path = safe_file(target, name)
        entry = git(target, 'ls-tree', head, '--', name).decode().split()
        if entry:
            if entry[0] != '100644' or not path.exists():
                raise ValueError('target file mode or existence mismatch')
            if path.read_bytes() != git(target, 'show', head + ':' + name):
                raise ValueError('allowlisted target dirty')
        elif path.exists():
            raise ValueError('untracked allowlisted target')
    if git(target, 'diff', '--cached', '--name-only', head, '--', *FILES).strip():
        raise ValueError('allowlisted index dirty')
    policy(blobs[FILES[0]])
    return target, stage, blobs


def plan(args):
    target, stage, blobs = validate(args)
    return dict(status='planned', order=list(FILES), policy='forced-OFF',
                confirm=f'apply:{target}:{args.expected_head}:{args.manifest_sha256}')


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
    p = commands.add_parser('plan')
    for name in ('target', 'expected-head', 'staging', 'manifest-sha256'):
        p.add_argument('--' + name, required=True)
    args = parser.parse_args()
    try:
        result = {'prepare': prepare, 'plan': plan}[args.command](args)
    except (ValueError, SyntaxError, TypeError, KeyError, OSError, subprocess.SubprocessError) as exc:
        print(json.dumps(dict(status='rejected', error=str(exc))))
        return 2
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
