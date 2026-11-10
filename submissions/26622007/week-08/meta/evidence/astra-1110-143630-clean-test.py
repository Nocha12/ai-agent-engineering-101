import sys,os,tempfile,subprocess,shutil
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import meta_harness as mh
scratch=tempfile.mkdtemp(prefix='astra-1110-143630-')
try:
 copy=mh.test_copy(scratch)
 env={k:v for k,v in mh.test_env().items() if k not in mh.TEST_COPY_ENV}
 print('Harness clean working-tree copy; .market/memory/logs excluded except tracked fixtures; exact TEST_BOOT',flush=True)
 p=subprocess.run(mh.test_command(),cwd=copy,env=env,timeout=900)
 print('returncode',p.returncode,flush=True)
 sys.exit(p.returncode)
finally:
 shutil.rmtree(scratch,ignore_errors=True)
