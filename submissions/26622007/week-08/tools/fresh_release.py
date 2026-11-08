"""Publisher-owned full buyer code release; leaves the live checkout untouched."""
import hashlib
import copy
import json
import os
from pathlib import Path
import re
import shlex
import sys
import uuid

VERIFY_SOURCE = r'''
import hashlib,json,os,stat
from pathlib import Path
def verify(root,digest):
    root=Path(root).absolute()
    if root.resolve()!=root or not root.is_dir():
        raise ValueError('canonical release directory required')
    if not isinstance(digest,str) or len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest):
        raise ValueError('full release digest required')
    def read(path):
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        with os.fdopen(fd,'rb') as stream:
            info=os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_mode&0o222 or info.st_uid!=root.stat().st_uid:
                raise ValueError('publisher-owned read-only file required')
            data=stream.read(16*1024*1024+1)
        if len(data)>16*1024*1024:
            raise ValueError('release file too large')
        return data
    raw=read(root/'release.json')
    if hashlib.sha256(raw).hexdigest()!=digest:
        raise ValueError('release digest mismatch')
    manifest=json.loads(raw)
    if set(manifest)!={'version','commit','files'} or manifest['version']!=1 or not isinstance(manifest['files'],dict):
        raise ValueError('invalid release manifest')
    found=set()
    for directory,dirs,names in os.walk(root,followlinks=False):
        for name in ['.',*dirs]:
            path=Path(directory)/name
            info=path.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_mode&0o222 or info.st_uid!=root.stat().st_uid:
                raise ValueError('publisher-owned read-only directory required')
        for name in names:
            relative=str((Path(directory)/name).relative_to(root))
            if relative=='release.json':
                continue
            if relative not in manifest['files'] or hashlib.sha256(read(root/relative)).hexdigest()!=manifest['files'][relative]:
                raise ValueError('release file mismatch')
            found.add(relative)
    if found!=set(manifest['files']) or 'market_buyer.py' not in found:
        raise ValueError('incomplete release')
    return manifest['commit']
'''

exec(VERIFY_SOURCE)

BOOTSTRAP = VERIFY_SOURCE + r'''
import argparse,runpy,sys
p=argparse.ArgumentParser()
p.add_argument('--release',required=True)
p.add_argument('--sha256',required=True)
p.add_argument('--live-dir',required=True)
p.add_argument('--publisher-uid',required=True,type=int)
p.add_argument('args',nargs=argparse.REMAINDER)
a=p.parse_args()
if Path(a.release).lstat().st_uid!=a.publisher_uid:
    raise SystemExit('release publisher mismatch')
commit=verify(a.release,a.sha256)
root=Path(a.release).absolute()
os.environ['AGENT_TOOLS']=str(root/'tools')
os.environ['AGENT_RELEASE_LIVE_DIR']=str(Path(a.live_dir).absolute())
if os.environ.get('SHOP_MARKET_NAME'):
    os.environ.setdefault('AGENT_MEMORY_DIR',str(Path(a.live_dir).absolute()/'memory'/'agent'))
os.environ.setdefault('OPENROUTER_ENV_FILE',str(Path(a.live_dir).absolute().parent/'.env'))
sys.path.insert(0,str(root))
args=a.args[1:] if a.args[:1]==['--'] else a.args
sys.argv=[str(root/'market_buyer.py'),*args]
sys.stderr.write('buyer_release_verified sha256='+a.sha256+' commit='+commit+'\n')
runpy.run_path(str(root/'market_buyer.py'),run_name='__main__')
'''


def publish(root, files, commit):
    if not re.fullmatch('[0-9a-f]{40}', commit):
        raise ValueError('full source commit required')
    files = dict(files)
    if 'market_buyer.py' not in files:
        raise ValueError('buyer entry required')
    for name, data in files.items():
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*\.py|pricing\.json|tools/[A-Za-z_][A-Za-z0-9_]*/(?:[A-Za-z_][A-Za-z0-9_]*\.py|manifest\.json)', name):
            raise ValueError('unsupported release path')
        if not isinstance(data, bytes) or len(data) > 16 * 1024 * 1024:
            raise ValueError('bounded source bytes required')
        if name.endswith('.py'):
            compile(data, name, 'exec')
    manifest = dict(version=1, commit=commit,
                    files={name: hashlib.sha256(data).hexdigest() for name, data in sorted(files.items())})
    raw = json.dumps(manifest, sort_keys=True, separators=(',', ':')).encode()
    digest = hashlib.sha256(raw).hexdigest()
    root = Path(root).absolute()
    if root.parent.resolve() != root.parent:
        raise ValueError('canonical publisher parent required')
    root.mkdir(mode=0o700)
    for name, data in {**files, 'release.json': raw}.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        target.chmod(0o444)
    for directory, _, _ in os.walk(root, topdown=False):
        os.chmod(directory, 0o555)
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    verify(root, digest)
    return digest


def files_from_git(repo, commit, *, settings=None):
    from .fresh_snapshot import snapshot_from_git
    from .quote_owner_executor import git, repository, revision
    repo, commit = repository(repo), revision(commit)
    raw, _ = snapshot_from_git(repo, commit, settings=settings)
    files = {name: text.encode() for name, text in json.loads(raw)['files'].items()}
    prefix = 'submissions/26622007/week-08/'
    for row in git(repo, 'ls-tree', '-r', commit, '--', prefix + 'tools/').decode().splitlines():
        info, path = row.split('\t', 1)
        name = path[len(prefix):]
        if not re.fullmatch(r'tools/[A-Za-z_][A-Za-z0-9_]*/(?:[A-Za-z_][A-Za-z0-9_]*\.py|manifest\.json)', name):
            continue
        if info.split()[:2] != ['100644', 'blob']:
            raise ValueError('regular tool files required')
        files[name] = git(repo, 'show', commit + ':' + path)
    return files


def command(root, digest, python=sys.executable, *, live_dir=None):
    if not Path(python).is_absolute() or not re.fullmatch('[0-9a-f]{64}', digest):
        raise ValueError('trusted absolute interpreter and digest required')
    return shlex.join([str(python), '-I', '-B', '-c', BOOTSTRAP,
                       '--release', str(Path(root).absolute()), '--sha256', digest,
                       '--publisher-uid', str(Path(root).stat().st_uid),
                       '--live-dir', str(Path(live_dir or Path.cwd()).absolute()), '--'])


def join_config(original, buyer_command, live_dir):
    configured = copy.deepcopy(original)
    if not Path(live_dir).is_absolute() or not isinstance(buyer_command, str) or not buyer_command:
        raise ValueError('absolute live directory and pinned buyer command required')
    try:
        tokens = shlex.split(configured['market_buyer']['cmd'])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError('market buyer command required') from error
    if tokens[:1] == ['env']:
        tokens.pop(0)
    environment = []
    while tokens and re.match(r'^[A-Z_][A-Z0-9_]*=', tokens[0]):
        assignment = tokens.pop(0)
        name = assignment.split('=', 1)[0]
        if (name in ('AGENT_TOOLS', 'AGENT_RELEASE_LIVE_DIR')
                or re.search('KEY|TOKEN|SECRET|PASS|OWNER|AUTH|COOKIE|CRED', name)
                or not (name == 'HARNESS' or name.startswith(('BUYER_', 'AGENT_', 'SHOP_', 'MARKET_')))):
            raise ValueError('unsupported buyer environment assignment')
        environment.append(assignment)
    if (len(tokens) < 2 or not re.fullmatch(r'python(?:3(?:\.\d+)?)?', Path(tokens[0]).name)
            or tokens[1] != 'market_buyer.py'):
        raise ValueError('plain existing Python buyer command required')
    suffix = tokens[2:]
    expected = {'--shops': '{shops}', '--item': '{item}', '--budget': '{budget}', '--order': '{order}'}
    pairs = list(zip(suffix[::2], suffix[1::2]))
    if (len(suffix) % 2 or len(dict(pairs)) != len(pairs)
            or not {'--shops', '--item', '--budget'} <= set(dict(pairs))
            or any(expected.get(key) != value for key, value in pairs)):
        raise ValueError('existing kit placeholders required')
    for role, value in configured.items():
        if isinstance(value, dict) and isinstance(value.get('cmd'), str):
            value['cmd'] = 'cd ' + shlex.quote(str(live_dir)) + ' && ' + value['cmd']
    prefix = shlex.join(['env', *environment]) + ' ' if environment else ''
    configured['market_buyer']['cmd'] = ('cd ' + shlex.quote(str(live_dir)) + ' && '
        + prefix + buyer_command + ' ' + ' '.join(suffix))
    return configured


def publish_join(root, configured, aliases):
    root = Path(root).absolute()
    if root.parent.resolve() != root.parent:
        raise ValueError('canonical managed join parent required')
    for name, target in aliases.items():
        if (name in ('.', '..', 'agent.json', '.gitignore')
                or not re.fullmatch(r'[A-Za-z_.][A-Za-z0-9_.-]*', name) or not Path(target).is_absolute()):
            raise ValueError('simple aliases to existing absolute live paths required')
    raw = json.dumps(configured, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    root.mkdir(mode=0o700)
    for name, data in (('agent.json', raw), ('.gitignore', b'*.pem\n.market/\n')):
        fd = os.open(root / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        (root / name).chmod(0o444)
    for name, target in aliases.items():
        (root / name).symlink_to(target)
        os.chmod(root / name, 0o755, follow_symlinks=False)
    root.chmod(0o555)
    return hashlib.sha256(raw).hexdigest()


def select_join(store, generation):
    store, generation = Path(store).absolute(), Path(generation).absolute()
    config = generation / 'agent.json'
    if (store.resolve() != store or generation.resolve() != generation
            or generation.parent != store / 'join-generations'
            or store.stat().st_uid != os.getuid() or store.stat().st_mode & 0o022
            or generation.parent.stat().st_uid != os.getuid() or generation.parent.stat().st_mode & 0o022
            or generation.stat().st_uid != os.getuid() or generation.stat().st_mode & 0o222
            or not config.is_file() or config.is_symlink()
            or config.stat().st_uid != os.getuid() or config.stat().st_mode & 0o222):
        raise ValueError('exclusive publisher-managed join generation required')
    selector = store / 'current-join'
    if selector.exists() and not selector.is_symlink():
        raise ValueError('foreign selector is preserved')
    temporary = store / ('.join-select-' + uuid.uuid4().hex)
    created = False
    try:
        temporary.symlink_to(generation)
        created = True
        os.chmod(temporary, 0o755, follow_symlinks=False)
        os.replace(temporary, selector)
        fd = os.open(store, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        if created:
            temporary.unlink(missing_ok=True)
