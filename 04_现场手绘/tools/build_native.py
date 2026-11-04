"""Fresh, serialized offline Gowin builds with original 50 MHz constraints.

Standard library only. No device programming, serial port, or hardware access.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime
import hashlib
import html
import json
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import xml.etree.ElementTree as ET

import ctypes
from contextlib import contextmanager

from read_active_json import read_json

PART = "GW2A-LV55PG484C8/I7"
SDC_SHA256 = "b875aa623ef6342803daaf60d60b1abf7abf3383e9b8a578b6f9641618454503"
ROOT = Path(__file__).resolve().parents[1]
BUSY_NAMES = {"gw_sh.exe", "gowinsynthesis.exe", "gw_pnr.exe", "gowin_pnr.exe",
              "gw_place_and_route.exe", "gowinplaceandroute.exe", "synplify.exe",
              "synplify_pro.exe", "synpwrap.exe"}

def check_native_messages(report: str, pnr_log: str, console: str) -> list[str]:
    """Keep implementation errors fatal; record the IDE's unrelated cache-log error."""
    ignored = []
    retained = []
    for line in console.splitlines():
        if re.fullmatch(r"alog error: failed to open log file: '[^'\r\n]+/GowinSemi/IDE/cache/sh\.log'", line):
            ignored.append(line)
        else:
            retained.append(line)
    if len(ignored) > 1:
        raise ValueError("Repeated IDE cache-log errors require review")
    if re.search(r"\bERROR\b|DesRoute failed|unknown option", report + pnr_log + "\n".join(retained), re.I):
        raise ValueError("Native output contains an implementation/compiler error")
    return ignored


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name+".%s.%s.tmp"%(os.getpid(),time.monotonic_ns()))
    temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    for attempt in range(8):
        try:
            temporary.replace(path)
            return
        except OSError:
            if attempt==7:raise
            time.sleep(min(.02*2**attempt,.5))


def field(timing: str, label: str) -> str:
    match = re.search(r"<td[^>]*>\s*" + re.escape(label) + r"\s*</td>\s*<td[^>]*>(.*?)</td>", timing, re.S)
    if not match:
        raise ValueError("Missing timing field: " + label)
    return html.unescape(re.sub(r"<[^>]+>", "", match[1])).strip()


def table(timing: str, name: str) -> str:
    return timing.split(f'name="{name}"', 1)[1].split("</table>", 1)[0]


def timing_metrics(timing: str) -> dict:
    from synthesis_gate import rows
    perf=rows(table(timing,"Max_Frequency_Report"))
    freq=[r for r in perf if len(r)>3 and r[1]=="clk_50m"]
    if len(freq)!=1:raise ValueError("Missing/duplicate base-clock Fmax")
    clocks=rows(table(timing,"Clock_Report"))
    names={r[1] for r in clocks[1:] if len(r)>6}
    if names!={"clk_50m"}:raise ValueError("Unexpected clocks: "+str(names))
    base=[r for r in clocks[1:] if len(r)>6 and r[1]=="clk_50m"]
    if len(base)!=1 or base[0][3]!="20.000":raise ValueError("Base clock changed")
    result={"setup_violated_endpoints":int(field(timing,"Numbers of Setup Violated Endpoints")),
            "hold_violated_endpoints":int(field(timing,"Numbers of Hold Violated Endpoints")),
            "analyzed_endpoints":int(field(timing,"Numbers of Endpoints Analyzed")),
            "clock_target_mhz":float(freq[0][2].split("(")[0]),
            "final_fmax_mhz":float(freq[0][3].split("(")[0])}
    for kind in ("Setup", "Hold"):
        rows = re.findall(r"<tr\b[^>]*>(.*?)</tr>", table(timing, kind + "_Slack_Table"), re.S)
        slacks = []
        for row in rows:
            cells = re.findall(r"<td\b[^>]*>(.*?)</td>", row, re.S)
            if len(cells) >= 2:
                slacks.append(float(html.unescape(re.sub(r"<[^>]+>", "", cells[1])).strip()))
        if not slacks:
            raise ValueError("Missing " + kind + " path slack table")
        result["minimum_reported_" + kind.lower() + "_slack_ns"] = min(slacks)
    return result


def validate_timing(metrics: dict) -> None:
    if metrics["setup_violated_endpoints"] or metrics["hold_violated_endpoints"]:
        raise ValueError("Setup/hold endpoints violate original 50 MHz timing")
    if metrics["analyzed_endpoints"] < 1000 or metrics["clock_target_mhz"] != 50 or metrics["final_fmax_mhz"] < 50:
        raise ValueError("Incomplete timing scope or final Fmax below 50 MHz")
    if metrics["minimum_reported_setup_slack_ns"] < 0 or metrics["minimum_reported_hold_slack_ns"] < 0:
        raise ValueError("Negative reported setup/hold slack")


def diagnostics(build: Path, project: str) -> dict:
    pnr = build / "impl/pnr"
    result = {}
    timing = pnr / f"{project}_tr_content.html"
    if timing.exists():
        result.update(timing_metrics(timing.read_text(errors="replace")))
    report = pnr / f"{project}.rpt.txt"
    if report.exists():
        text = report.read_text(errors="replace")
        result["resources"] = {}
        for label in ("Logic", "Register", "CLS", "BSRAM", "DSP", "PRIMARY", "LW"):
            match = re.search(r"^\s*" + re.escape(label) + r"\s*\|\s*([^|]+)\|", text, re.M)
            if match:
                result["resources"][label] = match[1].strip()
        if "BSRAM" not in result["resources"]:
            # This Gowin version omits unused BSRAM in its physical table.
            # Require explicit zero in the native synthesis report and no RAM
            # primitive instances in the netlist used by this PnR run.
            synth_report = build / "impl/gwsynthesis" / f"{project}_syn.rpt.html"
            netlist = build / "impl/gwsynthesis" / f"{project}.vg"
            if synth_report.is_file() and netlist.is_file():
                summary = synth_report.read_text(errors="replace")
                zero = re.search(r'<td[^>]*>\s*BSRAM\s*</td>\s*<td[^>]*>\s*0\s*/\s*140\s*</td>', summary)
                primitive = re.search(r'^\s*(?:SP|SPX9|SDP|SDPX9|DP|DPX9|ROM|ROMX9|pROM|pROMX9)\s+(?:#\s*\(|\\?\w)', netlist.read_text(errors="replace"), re.M)
                if zero and not primitive:
                    result["resources"]["BSRAM"] = "0/140"
                    result["bsram_zero_basis"] = "PnR omits BSRAM; native synthesis explicitly reports 0/140; bound mapped netlist has no BSRAM primitive instances"
        version = re.search(r"<Tool Version>:\s*(.+)", text)
        result["tool_version"] = version[1].strip() if version else None
    return result


def source_inputs(project_file: Path) -> tuple[dict[str, str], dict]:
    """Bind only enabled local design inputs plus local headers and Tcl sources."""
    project_file = project_file.resolve()
    source = project_file.parent
    tree = ET.parse(project_file)
    device = tree.find('Device')
    if device is None or device.get('name') != 'GW2A-55C' or device.get('pn') != PART:
        raise ValueError('Unexpected J280 device/package/speed')
    paths = {project_file}
    enabled = []
    for entry in tree.findall('./FileList/File'):
        if entry.get('enable', '1') != '1':
            continue
        path = (source / entry.attrib['path']).resolve()
        if not path.is_relative_to(source) or path.is_symlink() or not path.is_file():
            raise ValueError('Enabled project input missing or outside flat project: ' + entry.attrib['path'])
        paths.add(path)
        enabled.append(path.relative_to(source).as_posix())
    for pattern in ('*.vh', '*.svh', '*.tcl'):
        paths.update(p for p in source.rglob(pattern)
                     if not {'impl','native_profile'}.intersection(p.relative_to(source).parts)
                     and p.name != 'build_native.tcl')
    pending = list(paths)
    inspected = set()
    while pending:
        path = pending.pop()
        if path in inspected:
            continue
        inspected.add(path)
        if path.suffix.lower() not in ('.v', '.sv', '.vh', '.svh'):
            continue
        for include in re.findall(r'^\s*`include\s+"([^"\r\n]+)"', path.read_text(errors='replace'), re.M):
            candidates = [(path.parent / include).resolve(), (source / include).resolve(), (source / 'src' / include).resolve()]
            candidates = [p for p in candidates if p.is_file() and p.is_relative_to(source)]
            if not candidates:
                raise ValueError('Unresolved local HDL include: ' + include)
            if candidates[0] not in paths:
                paths.add(candidates[0])
                pending.append(candidates[0])
    for path in paths:
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(source):
            raise ValueError('Unsafe source closure input: ' + str(path))
    sdcs = [p for p in paths if p.suffix.lower() == '.sdc']
    csts = [p for p in paths if p.suffix.lower() == '.cst']
    if len(sdcs) != 1 or len(csts) != 1 or sha(sdcs[0]) != SDC_SHA256:
        raise ValueError('Require one exact frozen quiet V3 SDC and one CST')
    hashes = {p.relative_to(source).as_posix(): sha(p) for p in sorted(paths)}
    return hashes, {'project': project_file.name, 'stem': project_file.stem,
                    'enabled_inputs': sorted(enabled), 'sdc': sdcs[0].relative_to(source).as_posix(),
                    'cst': csts[0].relative_to(source).as_posix(), 'part': PART}


def compiler_processes() -> list[dict]:
    result = subprocess.run(['tasklist', '/FO', 'CSV', '/NH'], capture_output=True,
                            text=True, errors='replace', check=True,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    return [dict(name=row[0], pid=int(row[1])) for row in csv.reader(result.stdout.splitlines())
            if len(row) > 1 and row[0].lower() in BUSY_NAMES]


def available_memory() -> int:
    class Memory(ctypes.Structure):
        _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong)] + [
            (name, ctypes.c_ulonglong) for name in ('total_phys', 'avail_phys', 'total_page',
            'avail_page', 'total_virtual', 'avail_virtual', 'avail_extended')]
    value = Memory()
    value.length = ctypes.sizeof(value)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(value)):
        raise ctypes.WinError()
    return value.avail_phys


@contextmanager
def compiler_mutex(record: dict, persist, independent_parallel=False):
    """Use the existing workstation lock; never terminate an unrelated job."""
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    kernel.CreateMutexW.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel.ReleaseMutex.argtypes = [ctypes.c_void_p]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.CreateMutexW(None, False, 'Local\\Codex_Gowin_Compiler_Serial')
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    acquired = False
    try:
        while True:
            state = kernel.WaitForSingleObject(handle, 30000)
            if state in (0, 128):
                acquired = True
                break
            if state != 258:
                raise RuntimeError('Compiler mutex wait failed')
            record.update(status='WAITING_COMPILER_MUTEX', last_heartbeat_unix=time.time())
            persist()
            print('Queued: waiting for the shared compiler mutex', flush=True)
        while True:
            busy = compiler_processes()
            if not busy:
                break
            if independent_parallel:
                if available_memory() < 8*1024**3:
                    raise ValueError('Independent concurrent build needs 8 GiB available RAM')
                record['concurrent_compilers_at_start'] = busy
                record['independent_parallel_profile'] = True
                break
            record.update(status='WAITING_OTHER_COMPILER', other_compilers=busy,
                          last_heartbeat_unix=time.time())
            persist()
            print('Queued: other native compiler ' + json.dumps(busy), flush=True)
            time.sleep(15)
        record.pop('other_compilers', None)
        yield
    finally:
        if acquired:
            kernel.ReleaseMutex(handle)
        kernel.CloseHandle(handle)


def native_tcl(project: str, top: str, usercode: str, python: Path, options: dict) -> str:
    lines = [f'open_project ./{project}.gprj', f'set_option -top_module {top}',
             'set_option -global_freq 50', 'set_option -use_sspi_as_gpio 1',
             f'set_option -user_code {usercode}', 'set_option -timing_driven 1',
             'set_option -show_all_warn 1']
    lines += [f'set_option -{key} {value}' for key, value in options.items()]
    lines += ['run syn', f'puts [exec {{{python.as_posix()}}} ./synthesis_gate.py --project {project}]',
              'run pnr', 'exit', '']
    return '\n'.join(lines)


def live(argv: list[str], build: Path, environment: dict, record: dict, persist) -> int:
    process = subprocess.Popen(argv, cwd=build, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               env=environment, creationflags=subprocess.CREATE_NO_WINDOW)
    record.update(compiler_pid=process.pid, status='RUNNING', last_heartbeat_unix=time.time())
    persist()
    print('COMPILER_PID=' + str(process.pid), flush=True)
    lines = queue.Queue()
    def consume():
        try:
            for line in iter(process.stdout.readline, b''):
                lines.put(line)
        finally:
            lines.put(None)
    threading.Thread(target=consume, daemon=True).start()
    beat = time.monotonic() + 30
    try:
        with (build / 'compile.log').open('wb') as log:
            while True:
                try:
                    line = lines.get(timeout=1)
                    if line is None:
                        break
                    log.write(line)
                    log.flush()
                    print(line.decode('utf-8', errors='replace'), end='', flush=True)
                except queue.Empty:
                    pass
                if time.monotonic() >= beat:
                    size = sum(p.stat().st_size for p in build.rglob('*') if p.is_file())
                    memory = available_memory()
                    if size > 2 * 1024**3 or shutil.disk_usage(build).free < 10 * 1024**3 or memory < 512 * 1024**2:
                        raise RuntimeError('Own build budget exceeded: 2 GiB files, 10 GiB disk reserve, 512 MiB free RAM')
                    record.update(private_native_bytes=size, available_memory_bytes=memory,
                                  last_heartbeat_unix=time.time(), elapsed_seconds=round(time.time()-record['started_unix'], 1))
                    try:
                        persist()
                    except OSError as error:
                        print('[watch] status write delayed: ' + str(error), flush=True)
                    print(f"[watch] elapsed={record['elapsed_seconds']}s; native_exit={process.poll()} (not an ETA)", flush=True)
                    beat = time.monotonic() + 30
        return process.wait()
    except BaseException:
        if process.poll() is None:
            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], check=False,
                           stdout=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
            process.wait()
        raise


def accept(build: Path, record: dict) -> dict:
    """Reparse actual native evidence; a compiler zero alone never passes."""
    stem = record['project_stem']
    if type(record.get('compiler_exit_code')) is not int or record['compiler_exit_code'] != 0:
        raise ValueError('Native compiler did not exit with integer zero')
    if record.get('reused_synthesis') is not False:
        raise ValueError('Fresh synthesis is required')
    hashes, metadata = source_inputs(build / record['project_file'])
    if hashes != record['source_sha256'] or metadata != record['project_metadata']:
        raise ValueError('Frozen build inputs changed')
    if sha(build / 'build_native.tcl') != record['build_tcl_sha256']:
        raise ValueError('Native Tcl changed')
    expected_tcl = native_tcl(stem, record['top'], record['usercode_hex'],
                             Path(record['python_path']), record['options'])
    if (build / 'build_native.tcl').read_text(encoding='ascii') != expected_tcl:
        raise ValueError('SYN/gate/PNR order or recorded options changed')
    helper = build / 'synthesis_gate.py'
    gate_file = build / 'SYNTHESIS_GATE.json'
    if sha(helper) != record['synthesis_gate_sha256']:
        raise ValueError('Frozen synthesis gate changed')
    if sha(build/'EXECUTED_BUILD_DRIVER.py') != record['driver_sha256'] or sha(build/'read_active_json.py') != record['json_reader_sha256']:
        raise ValueError('Executed build driver or JSON helper changed')
    gate = read_json(gate_file, root=build)
    from synthesis_gate import syn_timing
    reparsed = syn_timing((build / f'impl/gwsynthesis/{stem}_syn.rpt.html').read_text(errors='replace'))
    if gate.get('status') != 'PASS' or gate.get('project') != stem or any(gate.get(k) != v for k, v in reparsed.items()):
        raise ValueError('Synthesis gate differs from actual report')
    metrics = diagnostics(build, stem)
    validate_timing(metrics)
    pnr = build / 'impl/pnr'
    evidence = [pnr / f'{stem}{suffix}' for suffix in ('.rpt.txt', '.log', '.fs', '_tr_content.html')]
    evidence += [pnr / 'cmd.do', build / 'compile.log', build / 'build_native.tcl',
                 build / f'impl/gwsynthesis/{stem}.vg', build / f'impl/gwsynthesis/{stem}_syn.rpt.html',
                 build / f'impl/gwsynthesis/{stem}_syn_rsc.xml', helper, gate_file,
                 build / 'EXECUTED_BUILD_DRIVER.py', build / 'read_active_json.py']
    for path in evidence:
        if not path.is_file() or not path.stat().st_size:
            raise ValueError('Missing or empty native evidence: ' + str(path))
        if path not in (helper, build/'build_native.tcl', build/'EXECUTED_BUILD_DRIVER.py', build/'read_active_json.py') and path.stat().st_mtime + 2 < record['started_unix']:
            raise ValueError('Stale native evidence: ' + str(path))
    report, messages, console = [(pnr/f'{stem}.rpt.txt').read_text(errors='replace'),
                                (pnr/f'{stem}.log').read_text(errors='replace'),
                                (build/'compile.log').read_text(errors='replace')]
    ignored = check_native_messages(report, messages, console)
    if 'Placement and routing completed' not in messages or 'Bitstream generation completed' not in messages:
        raise ValueError('Explicit complete routing and bit generation required')
    unrouted = [line for line in (report+messages+console).splitlines() if re.search('unrout', line, re.I)]
    if any(not re.search(r'\b0\s+unrout', line, re.I) for line in unrouted):
        raise ValueError('Nonzero or unrecognized unrouted-net report')
    if PART not in report or not {'Logic', 'Register', 'CLS', 'BSRAM', 'PRIMARY', 'LW'}.issubset(metrics.get('resources', {})):
        raise ValueError('Incomplete physical resources or wrong part')
    for name, value in metrics['resources'].items():
        match = re.fullmatch(r'([\d.]+)/([\d.]+)', value)
        if not match or float(match[1]) > float(match[2]):
            raise ValueError('Invalid or over-capacity physical resource: ' + name)
    cmd = (pnr/'cmd.do').read_text(errors='replace')
    for option, relative in {'-d': f'impl/gwsynthesis/{stem}.vg', '-cst': metadata['cst'], '-sdc': metadata['sdc']}.items():
        match = re.search(r'^'+re.escape(option)+r'\s+(.+?)\s*$', cmd, re.M)
        if not match or Path(match[1].strip('"')).resolve() != (build/relative).resolve():
            raise ValueError('Native mapped/constraint binding failed: ' + option)
    for option, expected in {'-p': 'GW2A-55C-PBGA484-8', '-pn': PART,
                             **{'-'+k: v for k,v in record['options'].items() if k in ('place_option','route_option','route_maxfan')}}.items():
        if not re.search(r'^'+re.escape(option)+r'\s+'+re.escape(str(expected))+r'\s*$', cmd, re.M):
            raise ValueError('Native device/implementation option mismatch: ' + option)
    fs = pnr/f'{stem}.fs'
    with fs.open(encoding='ascii', errors='replace') as handle:
        header = ''.join(handle.readline() for _ in range(32))
    usercode = re.search(r'//UserCode:\s*0x([0-9a-fA-F]+)', header)
    if f'//Part Number: {PART}' not in header or '//Device-package: GW2A-55C-PBGA484' not in header:
        raise ValueError('FS header part mismatch')
    if not usercode or int(usercode[1],16) != int(record['usercode_hex'],16):
        raise ValueError('FS header UserCode mismatch')
    return {**metrics, 'accepted': True, 'bitstream': str(fs), 'bitstream_sha256': sha(fs),
            'user_code': '0x'+record['usercode_hex'], 'part': PART,
            'evidence_sha256': {p.relative_to(build).as_posix(): sha(p) for p in evidence},
            'nonfatal_ide_cache_log_errors': ignored, 'unrouted_mentions': unrouted,
            'scope': 'Original internal 50MHz STA; external ADC clock-to-data maximum and board skew were not supplied',
            'external_adc_timing_physically_verified': False, 'hardware_verified': False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--compiler', type=Path, required=True)
    parser.add_argument('--python', type=Path, default=Path(sys.executable))
    parser.add_argument('--workroot', type=Path, required=True)
    parser.add_argument('--status', type=Path, required=True)
    parser.add_argument('--top', default='forest_d1_top')
    parser.add_argument('--usercode', required=True)
    parser.add_argument('--opt-goal', choices=('auto','area','timing'), default='area')
    parser.add_argument('--map-option', choices=(1,2,3,4), type=int, default=2)
    parser.add_argument('--place-option', choices=(0,1,2,3,4), type=int, default=2)
    parser.add_argument('--route-option', choices=(0,1,2), type=int, default=2)
    parser.add_argument('--route-maxfan', '--maxfan', choices=range(1,101), type=int, default=60)
    parser.add_argument('--independent-profile-parallel', action='store_true',
                        help='Allow existing compiler jobs; this build still uses its own frozen sources, TEMP, APPDATA, and outputs')
    args = parser.parse_args()
    record = dict(accepted=False, status='PREPARING', reused_synthesis=False,
                  hardware_actions=0, driver_exit_code=1)
    build = None
    def persist():
        for path in ([build/'RESULT.json'] if build else []) + [args.status.resolve()]:
            write_json(path, record)
    try:
        if os.name != 'nt':
            raise ValueError('This native driver requires Windows')
        project, compiler, workroot = args.project.resolve(), args.compiler.resolve(), args.workroot.resolve()
        if not re.fullmatch(r'[A-Za-z0-9_]+', project.stem) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', args.top):
            raise ValueError('Project stem and top must be ASCII HDL identifiers')
        if not str(workroot).isascii() or any(c.isspace() for c in str(workroot)) or workroot.is_relative_to(project.parent):
            raise ValueError('Independent ASCII workroot without spaces required')
        if not compiler.is_file() or compiler.name.lower() != 'gw_sh.exe' or not str(compiler).isascii():
            raise ValueError('An installed ASCII-path gw_sh.exe is required')
        python = args.python.resolve()
        if not python.is_file() or not str(python).isascii() or any(c in str(python) for c in '{}\r\n'):
            raise ValueError('An ASCII Python runtime is required by the Tcl gate')
        code = args.usercode.removeprefix('0x').removeprefix('0X')
        if not re.fullmatch(r'[0-9a-fA-F]{8}', code):
            raise ValueError('UserCode must be exactly eight hex digits')
        code = code.upper()
        hashes, metadata = source_inputs(project)
        if (project.parent/'build_native.tcl').exists():
            raise ValueError('build_native.tcl is reserved for the generated frozen build entry')
        options = dict(opt_goal=args.opt_goal, map_option=args.map_option, place_option=args.place_option,
                       route_option=args.route_option, route_maxfan=args.route_maxfan)
        record.update(source_directory=str(project.parent), source_sha256=hashes, project_metadata=metadata,
                      project_file=project.name, project_stem=project.stem, top=args.top, usercode_hex=code,
                      options=options, python_path=str(python), compiler_path=str(compiler), compiler_sha256=sha(compiler))
        persist()
        with compiler_mutex(record, persist, args.independent_profile_parallel):
            if available_memory() < 2*1024**3 or shutil.disk_usage(workroot.anchor).free < 10*1024**3:
                raise ValueError('Require at least 2 GiB available RAM and 10 GiB disk reserve before native start')
            if source_inputs(project) != (hashes, metadata):
                raise ValueError('Working inputs changed while waiting for compiler')
            workroot.mkdir(parents=True, exist_ok=True)
            build = workroot/(project.stem+'_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
            build.mkdir()
            for relative in hashes:
                destination = build/relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(project.parent/relative, destination)
            if source_inputs(build/project.name) != (hashes, metadata):
                raise ValueError('Source closure changed during snapshot')
            helper = Path(__file__).with_name('synthesis_gate.py')
            shutil.copyfile(helper, build/'synthesis_gate.py')
            shutil.copyfile(Path(__file__), build/'EXECUTED_BUILD_DRIVER.py')
            json_helper = Path(__file__).with_name('read_active_json.py')
            shutil.copyfile(json_helper, build/'read_active_json.py')
            (build/'build_native.tcl').write_text(native_tcl(project.stem,args.top,code,python,options), encoding='ascii')
            record.update(build_directory=str(build), synthesis_gate_sha256=sha(helper),
                          build_tcl_sha256=sha(build/'build_native.tcl'), started_unix=time.time(),
                          driver_sha256=sha(Path(__file__)), json_reader_sha256=sha(json_helper))
            write_json(build/'SOURCE.json', record)
            print('BUILD_DIRECTORY='+str(build), flush=True)
            print('STATUS_FILE='+str(args.status.resolve()), flush=True)
            environment = os.environ.copy()
            for name, subdir in {'TEMP':'temp','TMP':'temp','LOCALAPPDATA':'local','APPDATA':'roaming'}.items():
                destination = build/'native_profile'/subdir
                destination.mkdir(parents=True, exist_ok=True)
                environment[name] = str(destination)
            (build/'native_profile/local/GowinSemi/IDE/cache').mkdir(parents=True, exist_ok=True)
            if compiler_processes() and not args.independent_profile_parallel:
                raise RuntimeError('Another native compiler started after the serial idle check')
            record['compiler_exit_code'] = live([str(compiler),'build_native.tcl'],build,environment,record,persist)
            record['working_source_changed_after_snapshot'] = source_inputs(project) != (hashes,metadata)
            if record['working_source_changed_after_snapshot']:
                raise ValueError('Working project inputs changed during compilation')
            record.update(accept(build,record), status='ACCEPTED', driver_exit_code=0)
        return 0
    except (Exception, KeyboardInterrupt) as error:
        record.update(status='CANCELLED' if isinstance(error,KeyboardInterrupt) else 'FAILED',
                      reason=str(error), driver_exit_code=1)
        if build:
            try:
                record['final_diagnostics'] = diagnostics(build,record['project_stem'])
            except Exception as diagnostic_error:
                record['diagnostic_error'] = str(diagnostic_error)
        print('BUILD_NOT_ACCEPTED='+str(error), flush=True)
        return 1
    finally:
        record['finished_unix'] = time.time()
        persist()
        print('DRIVER_EXIT_CODE='+str(record['driver_exit_code']), flush=True)
        if build:
            print('RESULT='+str(build/'RESULT.json'), flush=True)


if __name__ == '__main__':
    raise SystemExit(main())


