"""Opt-in pinned buyer image; never overwrites a mutable checkout or its Git state.

The bootstrap must itself be trusted (e.g. a kit command captured at startup).
This is integrity against filesystem writers, not an OS/process sandbox. Runtime
market/memory IO and third-party interpreter packages remain outside the image.
"""
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import sys
import uuid

LIMIT = 16 * 1024 * 1024

# Self-contained so the existing kit can hold it in its already-loaded command.
# No mutable Python launcher file or checkout import is used by the child.
BOOTSTRAP = r'''
import argparse,builtins,hashlib,importlib.abc,importlib.util,io,json,os,re,stat,sys,types
p=argparse.ArgumentParser()
p.add_argument('--image',required=True)
p.add_argument('--sha256',required=True)
p.add_argument('args',nargs=argparse.REMAINDER)
a=p.parse_args()
if not re.fullmatch('[0-9a-f]{64}',a.sha256):
    raise SystemExit('invalid snapshot digest')
fd=os.open(a.image,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
with os.fdopen(fd,'rb') as stream:
    if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
        raise SystemExit('regular snapshot required')
    raw=stream.read(16*1024*1024+1)
if len(raw)>16*1024*1024 or hashlib.sha256(raw).hexdigest()!=a.sha256:
    raise SystemExit('snapshot digest mismatch')
m=json.loads(raw)
if set(m)!={'version','commit','entry','helper','files'} or type(m['version']) is not int or m['version']!=1 or m['entry']!='market_buyer.py':
    raise SystemExit('invalid snapshot manifest')
if not re.fullmatch('[0-9a-f]{40}',m['commit']):
    raise SystemExit('invalid snapshot commit')
if not isinstance(m['files'],dict) or not m['files']:
    raise SystemExit('invalid snapshot files')
files={}
for name,value in m['files'].items():
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*\.py|pricing\.json',name) or not isinstance(value,str):
        raise SystemExit('invalid snapshot path')
    files[name]=value.encode('utf-8')
if not re.fullmatch('buyer_fresh_rotation_[0-9a-f]{64}',m['helper']):
    raise SystemExit('invalid versioned helper')
if hashlib.sha256(files[m['helper']+'.py']).hexdigest()!=m['helper'].rsplit('_',1)[1]:
    raise SystemExit('helper identity mismatch')
literal=('import '+m['helper']+' as buyer_fresh_rotation').encode()
entry=files[m['entry']]
if (literal not in entry.splitlines() and b'buyer_fresh_rotation' in entry) or 'buyer_fresh_rotation.py' in files:
    raise SystemExit('entry/helper binding mismatch')
files=types.MappingProxyType(files)
# Keep the existing runtime data/key-path conventions. These source filenames
# are provenance only: local imports and declared resource reads use RAM bytes.
prefix=os.path.abspath(os.getcwd())+os.sep
modules={name[:-3]:data for name,data in files.items() if name.endswith('.py')}
if set(modules)&(set(sys.stdlib_module_names)|set(sys.modules)):
    raise SystemExit('snapshot module collision')
sys.stderr.write('buyer_snapshot_verified sha256='+a.sha256+' commit='+m['commit']+'\n')
class Loader(importlib.abc.MetaPathFinder,importlib.abc.Loader):
    def find_spec(self,fullname,path=None,target=None):
        return importlib.util.spec_from_loader(fullname,self) if fullname in modules else None
    def create_module(self,spec):
        return None
    def get_filename(self,fullname):
        return prefix+fullname+'.py'
    def get_source(self,fullname):
        return modules[fullname].decode('utf-8')
    def exec_module(self,module):
        module.__file__=self.get_filename(module.__name__)
        exec(compile(modules[module.__name__],module.__file__,'exec'),module.__dict__)
loader=Loader()
sys.meta_path.insert(0,loader)
# Direct open()/Path.read_text() of declared source/policy/config consumes the
# SAME verified bytes as imports. Other runtime data IO retains its old paths.
original_open,original_io_open=builtins.open,io.open
def memory_open(original,file,mode='r',buffering=-1,encoding=None,errors=None,newline=None,closefd=True,opener=None):
    if isinstance(file,(str,bytes,os.PathLike)):
        name=os.path.abspath(os.fsdecode(file))
        if name.startswith(prefix):
            relative=name[len(prefix):]
            if relative in files:
                if mode not in ('r','rt','rb') or opener is not None:
                    raise PermissionError('snapshot resource is read-only')
                stream=io.BytesIO(files[relative])
                return stream if mode=='rb' else io.TextIOWrapper(stream,encoding=encoding or 'utf-8',errors=errors,newline=newline)
    return original(file,mode,buffering,encoding,errors,newline,closefd,opener)
builtins.open=lambda *args,**kw:memory_open(original_open,*args,**kw)
io.open=lambda *args,**kw:memory_open(original_io_open,*args,**kw)
args=a.args[1:] if a.args[:1]==['--'] else a.args
sys.argv=[prefix+m['entry']]+args
namespace={'__name__':'__main__','__file__':sys.argv[0],'__package__':None,'__builtins__':builtins}
exec(compile(files[m['entry']],sys.argv[0],'exec'),namespace)
'''


def make_snapshot(files, commit, *, settings=None):
    """Return bytes + digest. Changes helper scalars in the artifact only."""
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("full source commit required")
    files = dict(files)
    if any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*\.py|pricing\.json", n) for n in files):
        raise ValueError("only top-level Python and pinned pricing resource supported")
    helper = files.pop("buyer_fresh_rotation.py").decode("utf-8")
    if settings is not None:
        if set(settings) != {"ENABLED", "START", "END", "STUDY"}:
            raise ValueError("complete helper settings required")
        if (type(settings["ENABLED"]) is not bool or type(settings["START"]) is not int
                or type(settings["END"]) is not int or not 0 < settings["START"] < settings["END"]
                or settings["END"] - settings["START"] != 1800
                or not isinstance(settings["STUDY"], str)
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", settings["STUDY"])):
            raise ValueError("strict flag and full thirty-minute window required")
        lines = helper.splitlines(keepends=True)
        assignments = {}
        for node in ast.parse(helper).body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                key = node.targets[0].id
                if key in settings:
                    if key in assignments or node.end_lineno != node.lineno:
                        raise ValueError("one literal scalar assignment required")
                    ast.literal_eval(node.value)
                    assignments[key] = node.lineno - 1
        if set(assignments) != set(settings):
            raise ValueError("missing helper scalar")
        for key, line in assignments.items():
            lines[line] = key + " = " + repr(settings[key]) + "\n"
        helper = "".join(lines)
    helper_bytes = helper.encode("utf-8")
    helper_name = "buyer_fresh_rotation_" + hashlib.sha256(helper_bytes).hexdigest()
    entry = files["market_buyer.py"].decode("utf-8")
    entry, count = re.subn(r"^import buyer_fresh_rotation$",
                          "import " + helper_name + " as buyer_fresh_rotation", entry, flags=re.M)
    # The retained-helper rollback entry predates the experiment entirely.
    # Keep that baseline source unchanged; its unused helper stays in the image.
    if count != 1 and (count != 0 or "buyer_fresh_rotation" in entry):
        raise ValueError("one literal fresh import, or baseline without fresh references, required")
    files["market_buyer.py"] = entry.encode("utf-8")
    files[helper_name + ".py"] = helper_bytes
    for name, data in files.items():
        if name.endswith(".py"):
            if name[:-3] in sys.stdlib_module_names:
                raise ValueError("stdlib shadowing is unsupported")
            compile(data, name, "exec")
    manifest = dict(version=1, commit=commit, entry="market_buyer.py", helper=helper_name,
                    files={n: b.decode("utf-8") for n, b in sorted(files.items())})
    data = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(data) > LIMIT:
        raise ValueError("snapshot too large")
    return data, hashlib.sha256(data).hexdigest()


def snapshot_from_git(repo, commit, *, settings=None):
    from .quote_owner_executor import git, repository, revision
    repo, commit = repository(repo), revision(commit)
    prefix = "submissions/26622007/week-08/"
    files = {}
    for row in git(repo, "ls-tree", "-r", commit, "--", prefix).decode().splitlines():
        info, path = row.split("\t", 1)
        name = path[len(prefix):]
        if "/" in name or name.startswith("test_") or not (name.endswith(".py") or name == "pricing.json"):
            continue
        if info.split()[:2] != ["100644", "blob"]:
            raise ValueError("regular source files required")
        files[name] = git(repo, "show", commit + ":" + path)
    return make_snapshot(files, commit, settings=settings)


def snapshot_command(image, digest, python=sys.executable):
    """Trusted kit startup command; no mutable -m/-c launcher file import."""
    if not re.fullmatch(r"[0-9a-f]{64}", digest) or not Path(python).is_absolute():
        raise ValueError("absolute trusted interpreter and full digest required")
    return shlex.join([str(python), "-I", "-B", "-c", BOOTSTRAP,
                       "--image", str(Path(image).absolute()), "--sha256", digest, "--"])


def publish_snapshot(path, data, digest):
    """Publish complete bytes to a NEW artifact name; never replace any file."""
    if len(data) > LIMIT or hashlib.sha256(data).hexdigest() != digest:
        raise ValueError("snapshot bytes/digest mismatch")
    path = Path(path).absolute()
    if path.parent.resolve() != path.parent:
        raise ValueError("canonical artifact directory required")
    temporary = path.parent / (".buyer-image-" + uuid.uuid4().hex)
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path, follow_symlinks=False)  # EEXIST preserves foreign bytes.
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        temporary.unlink(missing_ok=True)


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="operation", required=True)
    build = sub.add_parser("build")
    for name in ("repo", "commit", "output"):
        build.add_argument("--" + name, required=True)
    command = sub.add_parser("command")
    command.add_argument("--image", required=True)
    command.add_argument("--sha256", required=True)
    args = parser.parse_args()
    if args.operation == "build":
        data, digest = snapshot_from_git(args.repo, args.commit)
        publish_snapshot(args.output, data, digest)
        print(json.dumps(dict(sha256=digest, source_commit=args.commit)))
    else:
        print(snapshot_command(args.image, args.sha256))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
