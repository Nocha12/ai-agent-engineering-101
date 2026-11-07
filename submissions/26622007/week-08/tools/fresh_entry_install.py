"""Pinned fresh-only entry-last installer. Local preparation; no SSH or Git writes.

Not an admission lock or multi-file transaction. Existing helpers must be byte
identical; the one new runtime helper is immutable and retained on rollback.
CLI install/rollback require an exact plan token and separate operating approval.

Mutation additionally requires exclusive_writer=True: a caller attestation, NOT
a lock, CAS, approval, or proof. The operator must exclude every filesystem and
Git writer (including the meta model subprocess, stage/commit/undo and deployers)
for the full install/recovery/rollback interval and control the root and its
ancestors. Ordinary buyer readers need not participate. Noncooperating writers
or hostile directory replacement are unsupported; validation cannot close that
race. The current meta harness does not implement this contract.
"""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

try:
    from .quote_owner_executor import atomic_write, git, repository, revision, safe_file
except ImportError:  # direct CLI, same installed tools directory
    from quote_owner_executor import atomic_write, git, repository, revision, safe_file

PREFIX = "submissions/26622007/week-08/"
ENTRY = "market_buyer.py"
HELPER = "buyer_fresh_rotation.py"
FILES = ("fresh-rotation-contract.md", "test_buyer_fresh_rotation.py",
         "test_fresh_choice_boundary.py", ENTRY)
PAYLOAD = (HELPER,) + FILES
CONTROLS = {"tools/fresh_entry_install.py", "test_fresh_entry_install.py"}
CONTROLS.update({"tools/fresh_snapshot.py", "test_fresh_snapshot.py"})


def blob_id(data):
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def tree(repo, commit):
    rows = git(repo, "ls-tree", "-r", commit, "--", PREFIX).decode().splitlines()
    result = {}
    for row in rows:
        metadata, path = row.split("\t", 1)
        mode, kind, oid = metadata.split()
        result[path[len(PREFIX):]] = (mode, kind, oid)
    return result


def imports(data):
    result = set()
    for node in ast.walk(ast.parse(data)):
        if isinstance(node, ast.Import):
            result.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            result.add(node.module.split(".")[0])
    return result


def release(repo, base, candidate):
    """Read pinned Git objects only; reject any existing-helper or other delta."""
    repo, base, candidate = repository(repo), revision(base), revision(candidate)
    before, after = tree(repo, base), tree(repo, candidate)
    changed = {n for n in before.keys() | after.keys() if before.get(n) != after.get(n)}
    if changed - (set(PAYLOAD) | CONTROLS) or not set(PAYLOAD) <= changed:
        raise ValueError("not the pinned five-path fresh-only source transition")
    if any(n in before for n in PAYLOAD if n != ENTRY):
        raise ValueError("new-file-only contract required")
    if any(after.get(n, ())[:2] != ("100644", "blob") for n in PAYLOAD):
        raise ValueError("regular payload required")
    old = git(repo, "show", base + ":" + PREFIX + ENTRY)
    blobs = {n: git(repo, "show", candidate + ":" + PREFIX + n) for n in PAYLOAD}
    # Also reject hidden references in existing modules, including late imports.
    try:
        references = git(repo, "grep", "-l", "buyer_fresh_rotation", base, "--", PREFIX + "*.py")
    except subprocess.CalledProcessError as exc:
        if exc.returncode != 1:
            raise
        references = b""
    if references or imports(blobs[ENTRY]) - imports(old) != {"buyer_fresh_rotation"}:
        raise ValueError("old/new import boundary differs from contract")
    flags = [node.value for node in ast.parse(blobs[HELPER]).body
             if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "ENABLED" for t in node.targets)]
    if len(flags) != 1 or ast.literal_eval(flags[0]) is not False:
        raise ValueError("helper must remain source OFF")
    local_names = {Path(n).stem for n in before if "/" not in n and n.endswith(".py")}
    if imports(blobs[HELPER]) - (local_names | sys.stdlib_module_names):
        raise ValueError("new helper has an unstaged dependency")
    for n, data in blobs.items():
        if n.endswith(".py"):
            compile(data, n, "exec")
    common = {n: info[2] for n, info in before.items()
              if "/" not in n and n.endswith(".py") and n != ENTRY}
    return dict(base=base, candidate=candidate, old_entry=old, blobs=blobs, common=common)


def validate(root, spec, *, rollback=False):
    root = Path(root).absolute()
    if root.resolve() != root or not root.is_dir():
        raise ValueError("canonical existing directory required")
    for n, oid in spec["common"].items():
        if blob_id(safe_file(root, n).read_bytes()) != oid:
            raise ValueError("common source drift")
    for n in PAYLOAD:
        path = safe_file(root, n)
        expected = spec["old_entry"] if n == ENTRY else spec["blobs"][n]
        if n == ENTRY:
            allowed = (spec["old_entry"], spec["blobs"][ENTRY]) if rollback else (expected,)
            if path.read_bytes() not in allowed:
                raise ValueError("entry drift")
        elif path.exists() and path.read_bytes() != expected:
            raise ValueError("new-file collision or helper downgrade")
    if not (root / HELPER).exists():
        if rollback or list(root.glob("__pycache__/buyer_fresh_rotation*.pyc")) or (root / "buyer_fresh_rotation.pyc").exists():
            raise ValueError("missing helper or orphan bytecode collision")
    return root


def publish_helper(root, data):
    """Same-filesystem complete file, atomic no-clobber link; never downgrade."""
    target = safe_file(root, HELPER)
    if target.exists():
        if target.read_bytes() != data:
            raise ValueError("immutable helper collision")
        return
    temporary = root / (".fresh-stage-" + uuid.uuid4().hex)
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), 0o644)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, target, follow_symlinks=False)
        directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def require_exclusive_writer(exclusive_writer):
    if exclusive_writer is not True:
        raise ValueError("exclusive trusted writer contract required; current meta coordination unverified")


def apply(root, spec, *, write=atomic_write, exclusive_writer=False):
    require_exclusive_writer(exclusive_writer)
    root = validate(root, spec)
    publish_helper(root, spec["blobs"][HELPER])
    try:
        for n in FILES:
            # Recheck shared source and entry immediately before each replace.
            validate(root, spec)
            write(safe_file(root, n), spec["blobs"][n])
        return dict(status="installed", entry=blob_id(spec["blobs"][ENTRY]), helper_retained=True)
    except (OSError, ValueError) as exc:
        current = safe_file(root, ENTRY).read_bytes()
        recovery = []
        if current == spec["blobs"][ENTRY]:
            try:
                atomic_write(safe_file(root, ENTRY), spec["old_entry"])
            except OSError as restore_error:
                recovery.append(type(restore_error).__name__)
        elif current != spec["old_entry"]:
            recovery.append("entry drift; no overwrite")
        return dict(status="failed", error=type(exc).__name__, recovery_errors=recovery,
                    helper_retained=True)


def rollback(root, spec, *, exclusive_writer=False):
    require_exclusive_writer(exclusive_writer)
    root = validate(root, spec, rollback=True)
    atomic_write(safe_file(root, ENTRY), spec["old_entry"])
    # Never unlink a helper: new code opened before this replace may import late.
    return dict(status="rolled_back", helper_retained=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "install", "rollback"))
    for n in ("repo", "base", "candidate", "root"):
        parser.add_argument("--" + n, required=True)
    parser.add_argument("--confirm")
    parser.add_argument("--exclusive-writer", action="store_true",
                        help="attest external exclusion of all writers and trusted directories; not a lock or proof")
    args = parser.parse_args()
    try:
        spec = release(args.repo, args.base, args.candidate)
        root = validate(args.root, spec, rollback=args.command == "rollback")
        token = f"{args.command}:{root}:{args.base}:{args.candidate}"
        if args.command == "plan":
            result = dict(status="planned", helper_first=HELPER, entry_last=ENTRY,
                          install_confirm=f"install:{root}:{args.base}:{args.candidate}",
                          rollback_confirm=f"rollback:{root}:{args.base}:{args.candidate}")
        else:
            if args.confirm != token:
                raise ValueError("exact plan confirmation required; separate operating approval also required")
            result = (apply(root, spec, exclusive_writer=args.exclusive_writer) if args.command == "install"
                      else rollback(root, spec, exclusive_writer=args.exclusive_writer))
    except (OSError, ValueError, SyntaxError, subprocess.SubprocessError) as exc:
        print(json.dumps(dict(status="rejected", error=type(exc).__name__)))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 2 if result["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
