"""Fresh, serialized ADB1 offline Gowin builds with original 50 MHz constraints.

Standard library only. No device programming, serial port, or hardware access.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime
import hashlib
import html
import json
import math
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
FIRMWARE_USERCODE = "41444231"
LOW_MEMORY_WARNING_BYTES = 512 * 1024**2
RESOURCE_CHECK_INTERVAL_SECONDS = 30
# Frozen deployed A4F numerical, sampling, actuator and physical constraints.
PROTECTED_SHA256 = {
    "src/forest_d1_pid.v": "d91779196f25851f00d529e6848adb049b1fa2d70ca5501149e00392e1ebfdf9",
    "src/forest_d1_fp64.v": "9e24ccf029718f78bd4f9a9111072d3f28cc683fa1fb6c982237ac0759413b4c",
    "src/forest_d1_fp_functions.vh": "11ea724bdf52e6cd49f456eb2a78dffa95123e5c2305f243cea43eee8a1a7c8e",
    "src/forest_d1_run_inputs.v": "c1eb158404d0a03b4a9d1d8ca895d19ff5dd69fbf2d45e9772318dd3c02de842",
    "src/forest_d1_run_actuator.v": "42bdda196e5c7e7c6cf900ba202c3c4224e7bedeeccfbf8ea63ccf2430c99598",
    "src/j280_adc_capture_trace.v": "069fd990d6593d1766ff2e826184740ccd4db86a0ec2dfb3d7334d1e81b29cdf",
    "src/j280_encoder_velocity.v": "d4aba72acdb8b9a5a7d2f6adf766affd955a36a24fe340ab927b47b26ff94813",
    "src/j280_encoder_x4.v": "1d80cdf9be06f78ca0f186231027b830f92e467199bef1149ac1b52e314d89c1",
    "src/j280_pwm_motor_safe.v": "99dd05ce105945223542d42f793627a979d426cd551615b7c4c713bcd60d90a5",
    "src/forest_d1_swing_pwm40k.v": "58973290e142b16967783f656cf93da94f9d8138789a78b8c4c9f2707c013573",
    "src/forest_d1_board.sdc": "b875aa623ef6342803daaf60d60b1abf7abf3383e9b8a578b6f9641618454503",
    "src/j280_official_interface.cst": "22178bb76a8dd850c0e624d84970f14d5adda6c6fe5e0b3626fd8a7487422e9e"
}
PWM_REPRESENTATIONS = {
    '4461133661cfe39992def33af9b147eaface09a5cbf19604e37448753b6b117e': 'base_5000_multiply',
    '283f03d884a359cd13c8bff6c8d68a2f5665b9ee6d2680f8eee46f82c150d99a': 'exact_5000_shift_add',
}
PWM_PROOF_SHA256 = '0a7e63bbba0e4dceccdcb71896792f009f8058b360c9df96a0fbb9552778e32e'
SAMPLER_REPRESENTATIONS = {
    "a95a57e72231f5c4ccb2a6189b7f34e28bfc05949b5bb1d97d5b2b8acd3396d1": "base_divide_by_15",
    "eb1bc8ec98a614013a0d2b513aad6778d81353ca22e1b30428865b40d11094d5": "precomputed_reciprocal_98bit",
    "245ce8403e3dd951ac42588a1150fcd7ea5d8c6b6f4809cc012893121e939034": "precomputed_mean_same_edge_guard",
}
SAMPLER_PROOF_SHA256 = "67036b669df2c90f9e0e806afb3fbfa2a08d260f8b9492636919880443e81f6f"
SAMPLER_PROOFS_BY_REPRESENTATION = {
    "base_divide_by_15": None,
    "precomputed_reciprocal_98bit": SAMPLER_PROOF_SHA256,
    "precomputed_mean_same_edge_guard": "8030c4d5488269118b1525d4e3ac389c7fa1c9a37fa23cf0ac033ee0f8238662",
}
SAFETY_REPRESENTATIONS = {
    "20bc3e2e87943e216422e3c34a73526fbcc3c8a70a62ad2e9d83ab946f4924da": "base_calibration_inhibit",
    "516b2613b50c500f7e92fee917578a71acc40deb65bf27fa20aa0cc20688de85": "same_cycle_motor_calibration_replica",
    "966c7c49d68ecce6a622c359791406ea794aeda2763a7a84007debf704b14002": "same_cycle_motor_calibration_and_stop_replica",
}
SAFETY_PROOF_SHA256 = "6e5f0e6c8084c2b5c8ddb299b060afe10bb67f950e92e4b3a7c78f240ff7b45f"
SAFETY_PROOFS_BY_REPRESENTATION = {
    'base_calibration_inhibit': None,
    'same_cycle_motor_calibration_replica': SAFETY_PROOF_SHA256,
    'same_cycle_motor_calibration_and_stop_replica': 'f9adfae8acde61e92fe7fd2278589d78752a3a444332debbd36d0659e274bace',
}
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
    for kind in ("Setup", "Hold", "Recovery", "Removal", "Pulse"):
        name = "MIN_PULSE_WIDTH_TABLE" if kind == "Pulse" else kind + "_Slack_Table"
        path_rows = [r for r in rows(table(timing, name))[1:] if r and r[0].isdigit()]
        slacks = [float(r[1]) for r in path_rows]
        if any(not math.isfinite(s) for s in slacks):
            raise ValueError("Non-finite " + kind + " slack")
        if not slacks and kind in ("Setup", "Hold"):
            raise ValueError("Missing " + kind + " path slack table")
        result[kind.lower() + "_reported_path_count"] = len(path_rows)
        result["minimum_reported_" + kind.lower() + "_slack_ns"] = min(slacks) if slacks else None
        result[kind.lower() + "_reported_violations"] = sum(s < 0 for s in slacks)
        if slacks:
            worst = path_rows[slacks.index(min(slacks))]
            result[kind.lower() + "_worst_path"] = dict(zip(rows(table(timing, name))[0], worst))
    return result


def validate_timing(metrics: dict) -> None:
    if metrics["setup_violated_endpoints"] or metrics["hold_violated_endpoints"]:
        raise ValueError("Setup/hold endpoints violate original 50 MHz timing")
    if metrics["analyzed_endpoints"] < 1000 or metrics["clock_target_mhz"] != 50 or not math.isfinite(metrics["final_fmax_mhz"]) or metrics["final_fmax_mhz"] < 50:
        raise ValueError("Incomplete timing scope or final Fmax below 50 MHz")
    if metrics["minimum_reported_setup_slack_ns"] < 0 or metrics["minimum_reported_hold_slack_ns"] < 0:
        raise ValueError("Negative reported setup/hold slack")
    for kind in ("recovery", "removal", "pulse"):
        if metrics[kind + "_reported_violations"]:
            raise ValueError("Negative reported " + kind + " slack")


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
    baseline=json.loads((ROOT/'review/NUMERICAL_BASELINE.json').read_text(encoding='utf8'))
    for name,digest in baseline.items():
        if hashes.get('src/'+name)!=digest:raise ValueError('New R2 numerical baseline changed: '+name)
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


def observe_memory(record: dict) -> None:
    """Record host RAM pressure without refusing or terminating a build."""
    record['memory_policy'] = 'monitor_only_no_ram_abort'
    try:
        memory = available_memory()
    except OSError as error:
        record['memory_monitor_error'] = str(error)
        print('[watch] RAM observation unavailable: ' + str(error), flush=True)
        return
    record['available_memory_bytes'] = memory
    record['minimum_available_memory_bytes'] = min(memory, record.get('minimum_available_memory_bytes', memory))
    if memory < LOW_MEMORY_WARNING_BYTES:
        record['low_memory_warning_count'] = record.get('low_memory_warning_count', 0) + 1
        print(f'[watch] available RAM {memory} bytes below warning threshold {LOW_MEMORY_WARNING_BYTES}; monitoring only', flush=True)


@contextmanager
def compiler_mutex(record: dict, persist):
    """Use the existing workstation lock; never terminate an unrelated job."""
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    kernel.CreateMutexW.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel.ReleaseMutex.argtypes = [ctypes.c_void_p]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    profile_key=hashlib.sha256((str(Path(__file__).resolve())+json.dumps(record.get('options',{}),sort_keys=True)).encode()).hexdigest()[:16]
    mutex_name = 'Local\\Codex_Gowin_J280_Unified_20261029_'+profile_key+'_'+str(os.getpid()) if record.get('isolated_native_parallel') else 'Local\\Codex_Gowin_Compiler_Serial'
    record['compiler_mutex_name']=mutex_name
    handle = kernel.CreateMutexW(None, False, mutex_name)
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
            if record.get('isolated_native_parallel'):
                record['observed_other_native_compilers_at_start']=busy
                record['parallel_scope']='Own unique mutex; private source snapshot/impl/TEMP/TMP/APPDATA/LOCALAPPDATA. No other task or license modified.'
                persist()
                break
            if not busy:
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


def native_tcl(project: str, top: str, usercode: str, python: Path, options: dict, synthesis_only: bool = False) -> str:
    lines = [f'open_project ./{project}.gprj', f'set_option -top_module {top}',
             'set_option -global_freq 50', 'set_option -use_sspi_as_gpio 1',
             f'set_option -user_code {usercode}', 'set_option -timing_driven 1',
             'set_option -show_all_warn 1']
    lines += [f'set_option -{key} {value}' for key, value in options.items()]
    lines += ['run syn', f'puts [exec {{{python.as_posix()}}} ./synthesis_gate.py --project {project}]']
    if not synthesis_only:
        lines += ['run pnr']
    lines += ['exit', '']
    return '\n'.join(lines)


def owned_process_job(process: subprocess.Popen):
    """Keep launcher descendants owned even if the launcher exits before timeout."""
    from ctypes import wintypes
    class BasicLimits(ctypes.Structure):
        _fields_ = [('process_time', ctypes.c_longlong), ('job_time', ctypes.c_longlong),
                    ('flags', wintypes.DWORD), ('minimum_working_set', ctypes.c_size_t),
                    ('maximum_working_set', ctypes.c_size_t), ('active_processes', wintypes.DWORD),
                    ('affinity', ctypes.c_size_t), ('priority', wintypes.DWORD), ('scheduling', wintypes.DWORD)]
    class IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in
                    ('read_ops', 'write_ops', 'other_ops', 'read_bytes', 'write_bytes', 'other_bytes')]
    class ExtendedLimits(ctypes.Structure):
        _fields_ = [('basic', BasicLimits), ('io', IoCounters),
                    ('process_memory', ctypes.c_size_t), ('job_memory', ctypes.c_size_t),
                    ('peak_process_memory', ctypes.c_size_t), ('peak_job_memory', ctypes.c_size_t)]
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateJobObjectW(None, None)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    limits = ExtendedLimits()
    limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not kernel.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)) or not kernel.AssignProcessToJobObject(handle, wintypes.HANDLE(int(process._handle))):
        error = ctypes.WinError(ctypes.get_last_error())
        kernel.CloseHandle(handle)
        if process.poll() is None:
            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], check=False,
                           stdout=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
            process.wait()
        raise error
    return kernel, handle


def vendor_route_failure(line: str) -> dict | None:
    """Recognize explicit routing failures, without generic error matching."""
    value = line.strip()
    code = re.fullmatch(r'ERROR\s+\(PR0004\)\s*:\s*(.+)', value)
    summary = re.fullmatch(r'(?:There are total\s+)?(\d+)\s+unrouted nets\.?', value)
    if code:
        count = re.search(r'\b(\d+)\s+unrouted nets\b', code[1])
        return dict(line=value, code='PR0004', kind='routing_error_code', unrouted_nets=int(count[1]) if count else None)
    if value in ('Error: DesRoute failed.', 'DesRoute failed.'):
        return dict(line=value, code=None, kind='DesRoute_failed', unrouted_nets=None)
    if summary and int(summary[1]) > 0:
        return dict(line=value, code=None, kind='positive_unrouted_summary', unrouted_nets=int(summary[1]))
    return None


def live(argv: list[str], build: Path, environment: dict, record: dict, persist) -> int:
    native_start = time.monotonic()
    process = subprocess.Popen(argv, cwd=build, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               env=environment, creationflags=subprocess.CREATE_NO_WINDOW)
    job_kernel, job_handle = owned_process_job(process)
    record.update(compiler_pid=process.pid, status='RUNNING', native_started_unix=time.time(), last_heartbeat_unix=time.time())
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
    beat = time.monotonic() + RESOURCE_CHECK_INTERVAL_SECONDS
    try:
        with (build / 'compile.log').open('wb') as log:
            while True:
                if time.monotonic() - native_start > record.get('native_timeout_seconds', 900):
                    raise TimeoutError('Own native build exceeded its wall-clock budget')
                try:
                    line = lines.get(timeout=1)
                    if line is None:
                        break
                    log.write(line)
                    log.flush()
                    decoded = line.decode('utf-8', errors='replace')
                    print(decoded, end='', flush=True)
                    failure = vendor_route_failure(decoded)
                    if failure:
                        record.update(vendor_failure_line=failure['line'], vendor_failure_code=failure['code'],
                                      vendor_failure_kind=failure['kind'], vendor_unrouted_nets=failure['unrouted_nets'],
                                      vendor_failure_elapsed_seconds=round(time.monotonic()-native_start, 3))
                        try:
                            persist()
                        except OSError as error:
                            print('[watch] vendor failure status write delayed: ' + str(error), flush=True)
                        raise RuntimeError('Native vendor route failure: ' + failure['line'])
                except queue.Empty:
                    pass
                if time.monotonic() >= beat:
                    size = sum(p.stat().st_size for p in build.rglob('*') if p.is_file())
                    observe_memory(record)
                    disk_free = shutil.disk_usage(build).free
                    record.update(private_native_bytes=size, available_disk_bytes=disk_free, resource_phase='runtime',
                                  last_heartbeat_unix=time.time(), native_elapsed_seconds=round(time.monotonic()-native_start, 1))
                    if size > 2 * 1024**3:
                        record['resource_abort_cause'] = 'stage_files'
                    elif disk_free < 10 * 1024**3:
                        record['resource_abort_cause'] = 'disk_reserve'
                    try:
                        persist()
                    except OSError as error:
                        print('[watch] status write delayed: ' + str(error), flush=True)
                    if size > 2 * 1024**3:
                        raise RuntimeError(f'Own stage file budget exceeded: {size} bytes > {2 * 1024**3} bytes')
                    if disk_free < 10 * 1024**3:
                        raise RuntimeError(f'Own build disk reserve below minimum: {disk_free} bytes < {10 * 1024**3} bytes')
                    print(f"[watch] native_elapsed={record['native_elapsed_seconds']}s; native_exit={process.poll()} (not an ETA)", flush=True)
                    beat = time.monotonic() + RESOURCE_CHECK_INTERVAL_SECONDS
        remaining = record.get('native_timeout_seconds', 900) - (time.monotonic() - native_start)
        return process.wait(timeout=max(.01, remaining))
    except BaseException:
        if process.poll() is None:
            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], check=False,
                           stdout=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
            process.wait()
        raise
    finally:
        job_kernel.CloseHandle(job_handle)
        record.update(native_finished_unix=time.time(), native_elapsed_seconds=round(time.monotonic()-native_start, 3))


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
                             Path(record['python_path']), record['options'], record.get('synthesis_only', False))
    if (build / 'build_native.tcl').read_text(encoding='ascii') != expected_tcl:
        raise ValueError('SYN/gate/PNR order or recorded options changed')
    helper = build / 'synthesis_gate.py'
    gate_file = build / 'SYNTHESIS_GATE.json'
    if sha(helper) != record['synthesis_gate_sha256']:
        raise ValueError('Frozen synthesis gate changed')
    if sha(build/'EXECUTED_BUILD_DRIVER.py') != record['driver_sha256'] or sha(build/'read_active_json.py') != record['json_reader_sha256'] or sha(build/'check_native.py') != record['checker_sha256']:
        raise ValueError('Executed build driver or JSON helper changed')
    gate = read_json(gate_file, root=build)
    from synthesis_gate import syn_timing
    reparsed = syn_timing((build / f'impl/gwsynthesis/{stem}_syn.rpt.html').read_text(errors='replace'))
    if gate.get('status') != 'PASS' or gate.get('project') != stem or any(gate.get(k) != v for k, v in reparsed.items()):
        raise ValueError('Synthesis gate differs from actual report')
    resource = ET.parse(build / f'impl/gwsynthesis/{stem}_syn_rsc.xml').getroot()
    counts = {name: int(resource.attrib[name].split('(')[0]) for name in ('T_Lut', 'T_Alu', 'T_Register')}
    if gate.get('synthesis_resources') != counts or counts['T_Lut'] + counts['T_Alu'] > 54720 or counts['T_Register'] > 42000:
        raise ValueError('Synthesis resource gate differs from report or exceeds capacity')
    common_evidence = [build/'compile.log', build/'build_native.tcl',
        build/f'impl/gwsynthesis/{stem}.vg', build/f'impl/gwsynthesis/{stem}_syn.rpt.html',
        build/f'impl/gwsynthesis/{stem}_syn_rsc.xml', helper, gate_file,
        build/'EXECUTED_BUILD_DRIVER.py', build/'read_active_json.py', build/'check_native.py']
    for path in common_evidence:
        if not path.is_file() or not path.stat().st_size:
            raise ValueError('Missing or empty synthesis evidence: ' + str(path))
        if path.stat().st_mtime + 2 < record['started_unix']:
            raise ValueError('Stale synthesis evidence: ' + str(path))
    if record.get('working_source_changed_after_snapshot') is not False:
        raise ValueError('Working source unchanged proof is missing or failed')
    if record.get('synthesis_only'):
        check_native_messages('', '', (build/'compile.log').read_text(errors='replace'))
        if (build/'impl/pnr').exists():
            raise ValueError('Synthesis-only build unexpectedly contains PNR outputs')
        return dict(accepted=False, qualified=False, precheck_passed=True,
            synthesis=reparsed, synthesis_resources=counts,
            evidence_sha256={p.relative_to(build).as_posix(): sha(p) for p in common_evidence},
            scope='Fresh synthesis estimate only; routed50MHz qualification is still required', hardware_verified=False)
    metrics = diagnostics(build, stem)
    validate_timing(metrics)
    pnr = build / 'impl/pnr'
    evidence = [pnr / f'{stem}{suffix}' for suffix in ('.rpt.txt', '.log', '.fs', '_tr_content.html')]
    evidence += [pnr / 'cmd.do', *common_evidence]
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
    if PART not in report or not {'Logic', 'Register', 'CLS', 'BSRAM', 'DSP', 'PRIMARY', 'LW'}.issubset(metrics.get('resources', {})):
        raise ValueError('Incomplete physical resources or wrong part')
    for name, value in metrics['resources'].items():
        match = re.fullmatch(r'([\d.]+)/([\d.]+)', value)
        if not match or float(match[1]) > float(match[2]):
            raise ValueError('Invalid or over-capacity physical resource: ' + name)
    cmd = (pnr/'cmd.do').read_text(errors='replace')
    for flag in ('-bit', '-tr', '-timing', '-cst_error'):
        if not re.search(r'^' + re.escape(flag) + r'\s*$', cmd, re.M):
            raise ValueError('Required native implementation flag missing: ' + flag)
    frequency = re.search(r'^-global_freq\s+([\d.]+)\s*$', cmd, re.M)
    if not frequency or float(frequency[1]) != 50:
        raise ValueError('Native PNR clock changed')
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
    return {**metrics, 'accepted': True, 'qualified': True, 'bitstream': str(fs), 'bitstream_sha256': sha(fs),
            'user_code': '0x'+record['usercode_hex'], 'part': PART,
            'evidence_sha256': {p.relative_to(build).as_posix(): sha(p) for p in evidence},
            'nonfatal_ide_cache_log_errors': ignored, 'unrouted_mentions': unrouted,
            'unrouted_nets': 0, 'unrouted_basis': 'Native routing completed and no nonzero or unrecognized unrouted-net report',
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
    parser.add_argument('--usercode', default=FIRMWARE_USERCODE)
    parser.add_argument('--opt-goal', choices=('auto','area','timing'), default='timing')
    parser.add_argument('--map-option', choices=(1,2,3,4), type=int, default=2)
    parser.add_argument('--place-option', choices=(0,1,2,3,4), type=int, default=2)
    parser.add_argument('--route-option', choices=(0,1,2), type=int, default=2)
    parser.add_argument('--route-maxfan', '--maxfan', choices=range(1,101), type=int, default=30)
    parser.add_argument('--synthesis-maxfan', type=int, default=None,
                        help='Vendor fanout-based register replication guide; separate from route_maxfan')
    parser.add_argument('--resource-sharing', type=int, choices=(0,1), default=None)
    parser.add_argument('--retiming', type=int, choices=(0,1), default=None)
    parser.add_argument('--retiming-resource', choices=('none','all','bsram','dsp'), default=None)
    parser.add_argument('--synthesis-only', action='store_true', help='Fresh synthesis+gate precheck only; never routed qualification')
    parser.add_argument('--native-timeout-seconds', type=int, default=900)
    parser.add_argument('--isolated-parallel', action='store_true', help='Only this task: use an independent mutex and private native profile, preserve other tasks')
    args = parser.parse_args()
    record = dict(accepted=False, qualified=False, status='PREPARING', reused_synthesis=False,
                  hardware_actions=0, driver_exit_code=1, invoked_unix=time.time(), synthesis_only=args.synthesis_only,
                  native_timeout_seconds=args.native_timeout_seconds, isolated_native_parallel=args.isolated_parallel)
    build = None
    def persist():
        for path in ([build/'RESULT.json'] if build else []) + [args.status.resolve()]:
            write_json(path, record)
    try:
        if os.name != 'nt':
            raise ValueError('This native driver requires Windows')
        if not 1 <= args.native_timeout_seconds <= 3600:
            raise ValueError('Native timeout must be between1 and3600 seconds')
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
        if code != FIRMWARE_USERCODE:
            raise ValueError('Integrated ADB1 UserCode must be ' + FIRMWARE_USERCODE)
        hashes, metadata = source_inputs(project)
        if (project.parent/'build_native.tcl').exists():
            raise ValueError('build_native.tcl is reserved for the generated frozen build entry')
        options = dict(opt_goal=args.opt_goal, map_option=args.map_option, place_option=args.place_option,
                       route_option=args.route_option, route_maxfan=args.route_maxfan)
        if args.synthesis_maxfan is not None:
            if not 8<=args.synthesis_maxfan<=10000:raise ValueError('Synthesis fanout guide must be8..10000')
            options['maxfan']=args.synthesis_maxfan
        if args.resource_sharing is not None:options['resource_sharing']=args.resource_sharing
        if args.retiming is not None:options['retiming']=args.retiming
        if args.retiming_resource is not None:options['retiming_resource']=args.retiming_resource
        record.update(source_directory=str(project.parent), source_sha256=hashes, project_metadata=metadata,
                      project_file=project.name, project_stem=project.stem, top=args.top, usercode_hex=code,
                      options=options, python_path=str(python), compiler_path=str(compiler), compiler_sha256=sha(compiler))
        persist()
        record['queue_started_unix'] = time.time()
        with compiler_mutex(record, persist):
            record['queue_wall_seconds'] = round(time.time() - record['queue_started_unix'], 3)
            observe_memory(record)
            disk_free = shutil.disk_usage(workroot.anchor).free
            record.update(available_disk_bytes=disk_free, private_native_bytes=0, resource_phase='startup')
            if disk_free < 10*1024**3:
                record['resource_abort_cause'] = 'disk_reserve'
            persist()
            if disk_free < 10*1024**3:
                raise ValueError(f'Native start disk reserve below minimum: {disk_free} bytes < {10 * 1024**3} bytes')
            if source_inputs(project) != (hashes, metadata):
                raise ValueError('Working inputs changed while waiting for compiler')
            workroot.mkdir(parents=True, exist_ok=True)
            build = workroot/(project.stem+'_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'_'+str(os.getpid()))
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
            checker = Path(__file__).with_name('check_native.py')
            shutil.copyfile(checker, build/'check_native.py')
            json_helper = Path(__file__).with_name('read_active_json.py')
            shutil.copyfile(json_helper, build/'read_active_json.py')
            (build/'build_native.tcl').write_text(native_tcl(project.stem,args.top,code,python,options,args.synthesis_only), encoding='ascii')
            record.update(build_directory=str(build), synthesis_gate_sha256=sha(helper),
                          build_tcl_sha256=sha(build/'build_native.tcl'), started_unix=time.time(),
                          driver_sha256=sha(Path(__file__)), json_reader_sha256=sha(json_helper), checker_sha256=sha(checker))
            write_json(build/'SOURCE.json', record)
            print('BUILD_DIRECTORY='+str(build), flush=True)
            print('STATUS_FILE='+str(args.status.resolve()), flush=True)
            environment = os.environ.copy()
            for name, subdir in {'TEMP':'temp','TMP':'temp','LOCALAPPDATA':'local','APPDATA':'roaming'}.items():
                destination = build/'native_profile'/subdir
                destination.mkdir(parents=True, exist_ok=True)
                environment[name] = str(destination)
            (build/'native_profile/local/GowinSemi/IDE/cache').mkdir(parents=True, exist_ok=True)
            if compiler_processes() and not args.isolated_parallel:
                raise RuntimeError('Another native compiler started after the serial idle check')
            record['compiler_exit_code'] = live([str(compiler),'build_native.tcl'],build,environment,record,persist)
            record['working_source_changed_after_snapshot'] = source_inputs(project) != (hashes,metadata)
            if record['working_source_changed_after_snapshot']:
                raise ValueError('Working project inputs changed during compilation')
            record.update(accept(build,record), status='SYNTHESIS_ONLY_PASS' if args.synthesis_only else 'ACCEPTED', driver_exit_code=0)
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
        record['total_wall_seconds'] = round(record['finished_unix'] - record['invoked_unix'], 3)
        persist()
        print('DRIVER_EXIT_CODE='+str(record['driver_exit_code']), flush=True)
        if build:
            print('RESULT='+str(build/'RESULT.json'), flush=True)


if __name__ == '__main__':
    raise SystemExit(main())


