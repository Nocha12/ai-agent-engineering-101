"""Pinned full buyer release, including the baseline agent/tool fallback."""
import json
import os
from pathlib import Path
import shlex
import select
import subprocess
import sys
import tempfile
import unittest

from tools import fresh_release


class FreshRelease(unittest.TestCase):
    def test_release_executes_pinned_entry_and_resource(self):
        files = {
            "market_buyer.py": b"from pathlib import Path\nimport policy\nassert policy.VALUE == 7\nprint(Path(__file__).parent.joinpath('tools/probe/manifest.json').read_text())\n",
            "policy.py": b"VALUE = 7\n",
            "tools/probe/manifest.json": b'{"purpose":"fixture"}',
            "tools/probe/tool.py": b"print('fixture')\n",
            "pricing.json": b"{}",
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            release = root / "release"
            digest = fresh_release.publish(release, files, "a" * 40)
            self.assertEqual(fresh_release.verify(release, digest), "a" * 40)
            command = fresh_release.command(release, digest, sys.executable)
            process = subprocess.run(shlex.split(command), cwd=root, capture_output=True,
                                     text=True, timeout=10)
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertEqual(json.loads(process.stdout), {"purpose": "fixture"})
            self.assertFalse((root / "__pycache__").exists())

    def test_actual_baseline_singleton_prefetch_none_hurry_and_agent(self):
        repo = Path(__file__).resolve().parents[3]
        pin = "f01be3537c89ef087d743c2b3762ab99f6382f6a"
        files = fresh_release.files_from_git(repo, pin)
        files['agent_tools.py'] = Path(__file__).with_name('agent_tools.py').read_bytes()
        tail = b'if __name__ == "__main__":\n    sys.exit(main())'
        self.assertIn(tail, files['market_buyer.py'])
        probe = r'''
import socket,subprocess
def forbidden(*args,**kwargs):
    raise AssertionError('unexpected external boundary')
socket.socket=forbidden
httpx.get=httpx.post=httpx.request=forbidden
subprocess.run=subprocess.Popen=forbidden
assert harness.BACKEND=='release-fixture-no-backend'
calls=[]
def answer(prompt,schema,system,**kwargs):
    calls.append(kwargs.get('tag'))
    if 'action' in schema['properties']:
        return {'action':'finish','tool':'','args_json':'{}','reason':'fixture','evidence':['c0'],
                'final':{'shop':'b','reason':'fixture'}}
    return {'shop':'b','reason':'fixture'}
ask=answer
agent_loop.ask=answer
shops=[dict(name=n,url='http://'+n,reviews=[]) for n in ('a','b','c')]
memory={'trades':[]}
AGENT_DIR=None
assert choose_racing(shops[:1],memory,'fixture',100,None,rule_engine=True)[0]['name']=='a'
assert not calls
assert choose_racing(shops,memory,'fixture',100,None,rule_engine=True)[0]['name']=='b'
assert calls==['buyer-choose']
calls.clear()
assert choose(shops,memory,'fixture',100,hurry=lambda:True)[0]['name']=='b'
assert calls==['buyer-choose']
import agent_tools
tools=agent_tools.discover(md=None)
assert len(tools)==17
md=os.path.join(os.getcwd(),'agent-fixture')
os.makedirs(md)
versions={}
for name in tools:
    with open(os.path.join(agent_tools.TOOLS,name,'manifest.json')) as stream:
        manifest=json.load(stream)
    if isinstance(manifest.get('selftest'),dict):
        with open(os.path.join(agent_tools.TOOLS,name,'tool.py'),'rb') as stream:
            version=hashlib.sha256(stream.read()+json.dumps(manifest['selftest'],sort_keys=True).encode()).hexdigest()[:16]
        versions[name]={'version':version,'why':None,'at':0}
with open(os.path.join(md,'tool_selftest.json'),'w') as stream:
    json.dump(versions,stream)
assert agent_loop.decide('fixture',pick_schema(['a','b','c']),md,'buyer',{'c0':'fixture'})['shop']=='b'
assert agent_tools.RUNTIME_HERE==os.getcwd()
assert agent_tools.TOOLS!=os.path.join(os.getcwd(),'tools')
print(json.dumps({'baseline_paths':4,'tools':len(tools),'backend_calls':0}))
'''
        files['market_buyer.py'] = files['market_buyer.py'].replace(tail, probe.encode())
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            release, live = root / 'release', root / 'live'
            live.mkdir()
            hostile = live / 'tools' / 'hostile'
            hostile.mkdir(parents=True)
            (hostile / 'manifest.json').write_text('{"purpose":"mutable"}')
            (hostile / 'tool.py').write_text("raise AssertionError('mutable tool executed')")
            digest = fresh_release.publish(release, files, pin)
            env = dict(os.environ, HARNESS='release-fixture-no-backend', AGENT_TOOLS=str(live/'tools'))
            process = subprocess.run(shlex.split(fresh_release.command(release,digest,sys.executable,live_dir=live)),
                                     cwd=live,env=env,capture_output=True,text=True,timeout=15)
            self.assertEqual(process.returncode,0,process.stderr)
            self.assertEqual(json.loads(process.stdout),dict(baseline_paths=4,tools=17,backend_calls=0))

    def test_eight_independent_live_writers_cannot_change_held_release(self):
        entry = b"""import json,sys
from pathlib import Path
print('READY',flush=True)
assert sys.stdin.readline().strip()=='continue'
import late
root=Path(__file__).parent
assert late.VALUE=='pinned'
assert root.joinpath('tools/probe/tool.py').read_text()=="VALUE = 'pinned'\\n"
assert json.loads(root.joinpath('pricing.json').read_text())=={'value':'pinned'}
print('PINNED',flush=True)
"""
        files = {'market_buyer.py':entry,'late.py':b"VALUE = 'pinned'\n",
                 'tools/probe/tool.py':b"VALUE = 'pinned'\n",
                 'tools/probe/manifest.json':b'{"purpose":"pinned"}',
                 'pricing.json':b'{"value":"pinned"}'}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            live = root/'live'
            live.mkdir()
            release = root/'release'
            digest = fresh_release.publish(release,files,'a'*40)
            process = subprocess.Popen(shlex.split(fresh_release.command(release,digest,sys.executable,live_dir=live)),
                                       cwd=live,stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE,text=True)
            try:
                self.assertTrue(select.select([process.stdout],[],[],5)[0])
                self.assertEqual(process.stdout.readline().strip(),'READY')
                writer = "from pathlib import Path; import sys; root=Path(sys.argv[1]); name=sys.argv[2]; (root/name).write_text('foreign-'+name)"
                writers = [subprocess.Popen([sys.executable,'-I','-B','-c',writer,str(live),'writer'+str(index)])
                           for index in range(8)]
                for child in writers:
                    self.assertEqual(child.wait(timeout=5),0)
                for name in ('market_buyer.py','late.py','pricing.json'):
                    (live/name).write_text('foreign mutation')
                stdout,stderr = process.communicate('continue\n',timeout=5)
                self.assertEqual(process.returncode,0,stderr)
                self.assertEqual(stdout.strip(),'PINNED')
                self.assertEqual((live/'market_buyer.py').read_text(),'foreign mutation')
                self.assertEqual(len(list(live.glob('writer*'))),8)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate()

    def test_existing_destination_and_partial_crash_are_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)/'foreign'
            root.mkdir()
            (root/'market_buyer.py').write_bytes(b'foreign')
            with self.assertRaises(FileExistsError):
                fresh_release.publish(root,{'market_buyer.py':b'pass'},'a'*40)
            self.assertEqual((root/'market_buyer.py').read_bytes(),b'foreign')
            with self.assertRaises((OSError,ValueError)):
                fresh_release.verify(root,'a'*64)

    def test_tamper_missing_extra_symlink_and_writable_sources_fail_before_entry(self):
        for mutation in ('tamper','missing','extra','symlink','writable'):
            with self.subTest(mutation=mutation),tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)/'release'
                digest = fresh_release.publish(root,{'market_buyer.py':b"print('ENTRY')\n"},'a'*40)
                root.chmod(0o755)
                entry = root/'market_buyer.py'
                if mutation=='missing':
                    entry.unlink()
                elif mutation=='extra':
                    (root/'extra.py').write_text('pass')
                    (root/'extra.py').chmod(0o444)
                elif mutation=='symlink':
                    entry.unlink()
                    entry.symlink_to('/dev/null')
                else:
                    entry.chmod(0o644)
                    if mutation=='tamper':
                        entry.write_text('pass')
                        entry.chmod(0o444)
                root.chmod(0o555)
                process = subprocess.run(shlex.split(fresh_release.command(root,digest,sys.executable)),
                                         capture_output=True,text=True,timeout=5)
                self.assertNotEqual(process.returncode,0)
                self.assertNotIn('ENTRY',process.stdout)


if __name__ == "__main__":
    unittest.main()
