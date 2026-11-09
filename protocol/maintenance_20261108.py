"""Self-restoring one-shot 4U protocol trial; the system module path is read-only."""
from pathlib import Path
import datetime, hashlib, json, os, platform, shutil, stat, subprocess, sys, time
stage = Path(__file__).resolve().parent
assert stage.parent == Path('/var/tmp') and stage.name.startswith('mes-protocol-dkms-')
KERNEL = '6.8.0-147-generic'
STOCK_HASH = '229267a6acce562869e9da3f2c46b593c5a0724f96d368c0313f9be7160c74c4'
STOCK_SRC = '8CF47837F896F00244D880B'
STOCK_NOTE = '6e5ef1eba2e0d66b9eeb5f5b83eacba2d87ae25071b944c5c7f4d68559aa41c9'
BOOT = 'cab431ab-f956-4ad5-96aa-43704c67141c'
PATCH_SHA = '7e464827708cd51a938f4b12eff8d147a77b65a1f9bfc8aa65678b22c33a48d4'
PCI = {f'0000:{n}:00.0' for n in ['03','13','63','73','83','93','e3','f3']}
stock = Path('/lib/modules')/KERNEL/'updates/dkms/amdgpu.ko.zst'
sysmod = Path('/sys/module/amdgpu')
tested = stage/'dkms/amdgpu/7.1.9-2407772.24.04'/KERNEL/'x86_64/module/amdgpu.ko.zst'
receipt = json.loads((stage/'receipt.json').read_text())
tested_src = receipt['modules']['amdgpu.ko.zst']['srcversion']
assert tested_src != STOCK_SRC and receipt['patch_sha256'] == PATCH_SHA and len(receipt['modules']) == 9
def read(path): return Path(path).read_text().strip()
def digest(path):
    with Path(path).open('rb') as f: return hashlib.file_digest(f,'sha256').hexdigest()
def record(name, val):
    (stage/(name+'.json')).write_text(json.dumps(val,indent=2)+'\n')
def run(*args, timeout=30, check=True):
    return subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout,
                          check=check, text=True).stdout.strip()
def numa():
    value=read('/proc/sys/kernel/numa_balancing')
    if value != '0': raise RuntimeError('host NUMA balancing changed: '+value)
    return value
def src(): return read(sysmod/'srcversion') if (sysmod/'srcversion').exists() else None
def devices():
    return {p.name for p in Path('/sys/bus/pci/drivers/amdgpu').glob('????:??:??.?')}
def check_stock_disk():
    assert platform.node()=='cks4u' and platform.release()==KERNEL
    assert read('/proc/sys/kernel/random/boot_id')==BOOT and digest(stock)==STOCK_HASH
    assert Path(run('modinfo','-n','amdgpu')) == stock
def idle():
    nodes=[p for p in Path('/dev/dri').glob('*') if stat.S_ISCHR(p.stat().st_mode)]
    assert Path('/dev/kfd').exists() and len(list(Path('/dev/dri').glob('renderD*')))==8
    test=subprocess.run(['fuser','/dev/kfd',*map(str,nodes)],capture_output=True,text=True)
    if test.returncode!=1 or test.stdout.strip() or test.stderr.strip(): raise RuntimeError(f'DRM busy: {test.stdout} {test.stderr}')
    if read(sysmod/'refcnt')!='0' or list((sysmod/'holders').iterdir()) or run('docker','ps','-q'):
        raise RuntimeError('GPU module or containers busy')
def wait_devices():
    until=time.monotonic()+35
    while time.monotonic()<until:
        if devices()==PCI and Path('/dev/kfd').exists() and len(list(Path('/dev/dri').glob('renderD*')))==8:
            return
        time.sleep(.5)
    raise RuntimeError('eight GPU devices did not reappear: '+repr(devices()))
def snapshot():
    return {'numa':numa(),'boot':read('/proc/sys/kernel/random/boot_id'),'live_src':src(),
            'note_sha256':digest(sysmod/'notes/.note.gnu.build-id') if sysmod.exists() else None,
            'devices':sorted(devices()),'numactl':run('numactl','--show'),
            'cpu_affinity':sorted(os.sched_getaffinity(0)),'topology':run('lspci','-Dtv'),
            'gpu_numa_nodes':{x:read(Path('/sys/bus/pci/devices')/x/'numa_node') for x in sorted(PCI)}}
def sel():
    return [json.loads(line) for line in run('python3',str(stage/'sel.py'), timeout=15).splitlines()]
def restore():
    check_stock_disk()
    current=src()
    if current==tested_src:
        idle()
        run('rmmod','amdgpu',timeout=75)
    elif current not in (None,STOCK_SRC):
        raise RuntimeError('unexpected loaded driver '+str(current))
    if src() is None: run('modprobe','amdgpu',timeout=100)
    wait_devices()
    assert src()==STOCK_SRC and digest(sysmod/'notes/.note.gnu.build-id')==STOCK_NOTE
    record('restored',snapshot())
    print('STOCK_RESTORED', flush=True)
def probe():
    check_stock_disk(); wait_devices(); idle()
    assert src()==STOCK_SRC and digest(sysmod/'notes/.note.gnu.build-id')==STOCK_NOTE
    assert digest(tested)==receipt['modules']['amdgpu.ko.zst']['sha256']
    assert run('modinfo','-F','sig_key',str(tested))==run('modinfo','-F','sig_key',str(stock))
    assert run('modinfo','-F','vermagic',str(tested))==run('modinfo','-F','vermagic',str(stock))
    assert 'mes_tag_probe:' in run('modinfo','-p',str(tested))
    # Do not drop any configured stock module options when using the absolute test path.
    options=run('modprobe','--show-depends','amdgpu').splitlines()[-1].split()
    assert options==['insmod',str(stock)], options
    assert read('/sys/class/graphics/fb0/name')=='astdrmfb'
    saved=stage/'amdgpu.stock.ko.zst'
    if not saved.exists(): shutil.copy2(stock,saved)
    assert digest(saved)==STOCK_HASH
    record('before',snapshot()); record('bmc-before',sel())
    start=datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    record('start',{'utc':start,'numa_before':numa()})
    time.sleep(2);idle()
    try:
        print('UNLOADING_IDLE_STOCK',flush=True)
        run('rmmod','amdgpu',timeout=75)
        if src() is not None: raise RuntimeError('original still present')
        print('LOADING_TRANSIENT_TEST',flush=True)
        with (stage/'insmod.log').open('w') as log:
            worker=subprocess.Popen(['insmod',str(tested),'mes_tag_probe=1'],stdout=log,stderr=subprocess.STDOUT)
            record('launch',{'pid':worker.pid,'numa_after_start':numa(),'service_cpu_affinity':sorted(os.sched_getaffinity(0)),
                             'worker_status':Path(f'/proc/{worker.pid}/status').read_text() if Path(f'/proc/{worker.pid}/status').exists() else 'exited'})
            rc=worker.wait(timeout=100)
        if rc: raise RuntimeError('test insmod failed: '+read(stage/'insmod.log'))
        wait_devices()
        assert src()==tested_src
        record('active',snapshot())
        events=run('journalctl','-k','-b','--since',start,'--no-pager','-o','cat')
        (stage/'active-kernel.log').write_text(events+'\n')
        lines=[line for line in events.splitlines() if 'MES_TAG_PROBE' in line]
        record('samples',lines);print('\n'.join(lines),flush=True)
        if not lines: raise RuntimeError('no trial markers')
    finally:
        restore()
        final=run('journalctl','-k','-b','--since',start,'--no-pager','-o','short-iso-precise')
        (stage/'kernel.log').write_text(final+'\n')
        record('bmc-after',sel())
        record('after',snapshot())
if sys.argv[1]=='restore': restore()
elif sys.argv[1]=='run': probe()
else: raise RuntimeError('unexpected action')
