"""Use the harness's exact isolated tree, command, and environment; preserve full output."""
import os,sys,tempfile,subprocess,shutil,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import meta_harness
tag=sys.argv[1] if len(sys.argv)>1 else ''; base=Path('meta/evidence'); scratch=tempfile.mkdtemp(prefix='astra-tests-')
try:
 copy=meta_harness.test_copy(scratch)
 env={k:v for k,v in meta_harness.test_env().items() if k not in meta_harness.TEST_COPY_ENV}
 with (base/f'astra-1109-133041-tests{tag}.txt').open('w') as f:
  proc=subprocess.Popen(meta_harness.test_command(),cwd=copy,env=env,stdout=f,stderr=subprocess.STDOUT,start_new_session=True)
  try: code=proc.wait(timeout=900)
  except subprocess.TimeoutExpired:meta_harness.kill_group(proc);code=124;f.write('\nTIMEOUT 900s\n')
 (base/f'astra-1109-133041-test-result{tag}.json').write_text(json.dumps({'command':meta_harness.test_command(),'returncode':code,'isolated':True},ensure_ascii=False,indent=1))
 print('returncode',code)
finally:shutil.rmtree(scratch,ignore_errors=True)
