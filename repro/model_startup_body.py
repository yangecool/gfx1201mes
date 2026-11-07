# Adapted from the original successful r2 reproducer; see provenance.json.
from pathlib import Path
import subprocess,tempfile,time,datetime,json,signal,os
root=Path(tempfile.mkdtemp(prefix='cks4u-mes-startup-20261107-'))
bundle={'remote_directory':str(root),'files':{}}
def run(cmd,timeout=15):
 p=subprocess.run(cmd,capture_output=True,text=True,timeout=timeout)
 return {'returncode':p.returncode,'stdout':p.stdout,'stderr':p.stderr}
def status(msg):print('STATUS '+datetime.datetime.now(datetime.timezone.utc).isoformat()+' '+msg,flush=True)
assert Path('/proc/sys/kernel/numa_balancing').read_text().strip()=='0'
assert run(['docker','ps','-q'])['stdout'].strip()==''
assert run(['fuser','/dev/kfd'])['returncode']==1
start=datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
bundle['start_utc']=start
(root/'trace.bt').write_text(TRACE_SOURCE);(root/'sel.py').write_text(SEL_SOURCE)
bundle['files']['bmc-before.json']=run(['python3',str(root/'sel.py')])
assert bundle['files']['bmc-before.json']['returncode']==0
sel_entries_before=[json.loads(line)['entries'] for line in bundle['files']['bmc-before.json']['stdout'].splitlines() if json.loads(line).get('type')=='sel-info'][0]
bundle['files']['host-before.json']=run(['bash','-c','date -Is; cat /proc/sys/kernel/random/boot_id; cat /proc/sys/kernel/numa_balancing; lscpu -e=CPU,NODE; lspci -Dtv'])
stdout=(root/'trace.stdout').open('w');stderr=(root/'trace.stderr').open('w')
tracer=subprocess.Popen(['bpftrace','-q',str(root/'trace.bt')],stdout=stdout,stderr=stderr)
worker=None;name='mesmock-original-'+os.urandom(6).hex()
try:
 deadline=time.monotonic()+10
 while 'MES_TRACE_READY' not in (root/'trace.stdout').read_text():
  if tracer.poll() is not None:raise RuntimeError((root/'trace.stderr').read_text())
  if time.monotonic()>deadline:raise RuntimeError('Tracer readiness timeout')
  time.sleep(.1)
 cmd=['docker','run','--pull=never','--name',name,'--label','mesmock.original-driver=true','--network','host','--ipc','host','--shm-size','8g','--device','/dev/kfd','--device','/dev/dri','--cpuset-cpus','0-47,96-143','--cpuset-mems','0-1','--mount','type=bind,src=/disk/ssd1/models/Qwen3.8-Flash-Next-FP8,dst=/models/target,readonly','--mount','type=bind,src=/disk/ssd1/models/.flashnext_cache,dst=/cache','--mount',f'type=bind,src={root},dst=/reports','-e','HIP_VISIBLE_DEVICES=0,1,2,3,4,5,6,7','-e','ROCR_VISIBLE_DEVICES=0,1,2,3,4,5,6,7','-e','NCCL_P2P_LEVEL=SYS','-e','NCCL_MIN_NCHANNELS=32','-e','NCCL_DEBUG=WARN','-e','OMP_NUM_THREADS=1','-e','VLLM_ROCM_USE_AITER=1','-e','VLLM_USE_V2_MODEL_RUNNER=1','-e','VLLM_DISABLE_SHARED_EXPERTS_STREAM=1','-e','VLLM_ENABLE_V1_MULTIPROCESSING=1','-e','VLLM_WORKER_MULTIPROC_METHOD=spawn','-e','VLLM_CONFIG_ROOT=/cache/vllm-config','-e','AITER_JIT_DIR=/cache/aiter','-e','TRITON_CACHE_DIR=/cache/triton','-e','VLLM_FLASHNEXT_RECORD_DIR=/reports/receipts','-e','ROCP_TOOL_LIB=','-e','HSA_TOOLS_LIB=','-e','LD_PRELOAD=','--entrypoint','/usr/bin/python3','sha256:cb7ee131ccebeea694573d35223c5ff2fadc131907da985f2c03836000a52d2a','-m','vllm.entrypoints.openai.api_server','--model','/models/target','--served-model-name','mes-startup-probe','--tensor-parallel-size','8','--pipeline-parallel-size','1','--dtype','bfloat16','--kv-cache-dtype','fp8_e4m3','--trust-remote-code','--language-model-only','--mamba-cache-mode','align','--host','127.0.0.1','--port','18037','--max-model-len','32768','--max-num-seqs','64','--max-num-active-seqs','64','--max-num-batched-tokens','16394','--gpu-memory-utilization','0.90','--enable-chunked-prefill','--no-enable-prefix-caching','--optimization-level','0','--compilation-config','{"mode":"NONE","cudagraph_mode":"FULL_DECODE_ONLY","max_cudagraph_capture_size":64}','--disable-uvicorn-access-log']
 bundle['command']=cmd
 log=(root/'server.log').open('w')
 worker=subprocess.Popen(cmd,stdout=log,stderr=subprocess.STDOUT,text=True)
 bundle['numa_after_launch']=Path('/proc/sys/kernel/numa_balancing').read_text().strip()
 status('Started installed image without source overlays; '+str(root))
 deadline=time.monotonic()+480;last_report=0;last_bmc_check=0
 while worker.poll() is None and time.monotonic()<deadline:
  text=(root/'server.log').read_text(errors='replace')
  traces=(root/'trace.stdout').read_text(errors='replace')
  kernel_now=run(['journalctl','-k','-b','--since',start,'--no-pager','-o','cat'])
  if any(marker in kernel_now['stdout'] for marker in ('PCI SERR','Uncorrected (Fatal)','Uncorrectable (Fatal)','Uncorrectable (Non-Fatal)','GPU reset begin')):
   bundle['stop_reason']='kernel_health_error';break
  if time.monotonic()-last_bmc_check>5:
   current_bmc=run(['python3','-c',SEL_SOURCE.split('last=query',1)[0]])
   entries=[json.loads(line)['entries'] for line in current_bmc['stdout'].splitlines() if json.loads(line).get('type')=='sel-info']
   if current_bmc['returncode'] or not entries or entries[0]!=sel_entries_before:
    bundle['stop_reason']='bmc_changed_or_unreadable';break
   last_bmc_check=time.monotonic()
  if Path('/proc/sys/kernel/numa_balancing').read_text().strip()!='0':bundle['stop_reason']='numa_changed';break
  if 'MES_ERROR_BRANCH ' in traces or 'MES_RETURN ' in traces:
   bundle['stop_reason']='mes_failure_captured';status('MES failure captured; stopping this probe');break
  if 'Application startup complete' in text:
   bundle['stop_reason']='startup_completed';status('Application startup complete');break
  if time.monotonic()-last_report>30:
   status(' | '.join(text.splitlines()[-2:])[-700:]);last_report=time.monotonic()
  time.sleep(1)
 if worker.poll() is not None:bundle['stop_reason']='process_exited';bundle['worker_exit_before_stop']=worker.returncode
 elif 'stop_reason' not in bundle:bundle['stop_reason']='bounded_timeout'
 bundle['files']['container-inspect.json']=run(['docker','inspect',name])
 status('Stop reason: '+bundle['stop_reason'])
finally:
 if worker is not None:
  bundle['files']['stop.json']=run(['docker','stop','-t','20',name],timeout=30)
  try:worker.wait(timeout=10)
  except subprocess.TimeoutExpired:pass
  bundle['files']['remove-owned-container.json']=run(['docker','rm',name])
  if 'log' in locals():log.close()
  bundle['files']['server.log']=(root/'server.log').read_text(errors='replace')
 if tracer.poll() is None:tracer.send_signal(signal.SIGINT)
 try:tracer.wait(timeout=8)
 except subprocess.TimeoutExpired:tracer.terminate();tracer.wait(timeout=5)
 stdout.close();stderr.close()
 bundle['files']['trace.stdout']=(root/'trace.stdout').read_text()
 bundle['files']['trace.stderr']=(root/'trace.stderr').read_text()
 bundle['files']['kernel.json']=run(['journalctl','-k','-b','--since',start,'--no-pager','-o','short-iso-precise'])
 bundle['files']['bmc-after.json']=run(['python3',str(root/'sel.py')])
 bundle['files']['host-after.json']=run(['bash','-c','date -Is; cat /proc/sys/kernel/random/boot_id; cat /proc/sys/kernel/numa_balancing; fuser -v /dev/kfd; docker ps --format "{{.ID}} {{.Names}}"'])
 bundle['numa_after']=Path('/proc/sys/kernel/numa_balancing').read_text().strip()
print('BUNDLE '+json.dumps(bundle),flush=True)
