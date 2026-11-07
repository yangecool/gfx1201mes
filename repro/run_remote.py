#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Run the original, bounded eight-GPU MES reproducer on an idle CKS4U.

Without --execute, only render the self-contained remote program locally.
This runner never builds, installs, signs, reloads, or registers a driver.
The instruction-offset probe is pinned to the original installed module hash;
it must not be reused on a patched or otherwise different driver binary.
"""

import argparse
import json
from pathlib import Path
import subprocess


def render():
    folder = Path(__file__).resolve().parent
    provenance = json.loads((folder / "provenance.json").read_text())
    expected = provenance["expected_installed_module_sha256"]
    expected_build_id = provenance["expected_loaded_build_id_note_sha256"]
    guard = f'''
import hashlib, pathlib, socket, subprocess
assert pathlib.Path('/proc/sys/kernel/osrelease').read_text().strip() == '6.8.0-147-generic'
assert pathlib.Path('/sys/module/amdgpu/version').read_text().strip() == '7.1.9.31600000'
module = pathlib.Path(subprocess.check_output(['modinfo','-n','amdgpu'],text=True).strip())
assert hashlib.sha256(module.read_bytes()).hexdigest() == {expected!r}, 'Instruction-offset probe requires the exact original module'
assert hashlib.sha256(pathlib.Path('/sys/module/amdgpu/notes/.note.gnu.build-id').read_bytes()).hexdigest() == {expected_build_id!r}, 'Loaded module build ID does not match the original probe'
assert pathlib.Path('/disk/ssd1/models/Qwen3.8-Flash-Next-FP8').is_dir()
assert pathlib.Path('/disk/ssd1/models/.flashnext_cache').is_dir()
subprocess.run(['docker','image','inspect','sha256:cb7ee131ccebeea694573d35223c5ff2fadc131907da985f2c03836000a52d2a'],stdout=subprocess.DEVNULL,check=True)
with socket.socket() as sock:
    sock.bind(('127.0.0.1',18037))
'''
    return (guard + "\nTRACE_SOURCE=" + repr((folder / "mes_trace_error_only.bt").read_text())
            + "\nSEL_SOURCE=" + repr((folder / "read_sel.py").read_text()) + "\n"
            + (folder / "model_startup_body.py").read_text())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="CKS4U")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    source = render()
    compile(source, "mesmock-remote-program", "exec")
    (output / "rendered_remote_program.py").write_text(source)
    if not args.execute:
        print(json.dumps({"executed": False, "rendered": str(output / "rendered_remote_program.py")}))
        return
    command = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", args.host, "python3 -u -"]
    bundle = None
    # No wall-clock timeout here: the remote program bounds the workload and
    # owns its cleanup. Killing SSH at a local deadline could orphan its job.
    with (output / "orchestration.stderr").open("w") as error_log, \
            (output / "orchestration.stdout").open("w") as log:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=error_log, text=True)
        process.stdin.write(source)
        process.stdin.close()
        for line in process.stdout:
            log.write(line)
            log.flush()
            if line.startswith("BUNDLE "):
                bundle = json.loads(line[len("BUNDLE "):])
            else:
                print(line, end="", flush=True)
        returncode = process.wait()
    if bundle is not None:
        (output / "bundle.json").write_text(json.dumps(bundle, indent=2) + "\n")
        for name, record in bundle["files"].items():
            if Path(name).name != name:
                raise RuntimeError("unexpected remote result path")
            content = record if isinstance(record, str) else json.dumps(record, indent=2) + "\n"
            (output / name).write_text(content)
        print(json.dumps({"stop_reason": bundle.get("stop_reason"), "numa_after": bundle.get("numa_after"),
                          "results": str(output)}, ensure_ascii=False))
    if returncode or bundle is None:
        raise SystemExit(f"remote reproducer failed ({returncode}); inspect {output / 'orchestration.stderr'}")


if __name__ == "__main__":
    main()
