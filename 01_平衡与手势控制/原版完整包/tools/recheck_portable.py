"""Verify immutable native evidence after relocation, without executing EDA."""
from pathlib import Path
import argparse
import hashlib
import importlib.util
import json
import re
import sys


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build', type=Path, required=True)
    args = parser.parse_args()
    build = args.build.resolve()
    record = json.loads((build/'RESULT.json').read_text('utf-8'))
    assert record['status'] == 'ACCEPTED' and record['accepted'] and record['qualified']
    assert type(record['compiler_exit_code']) is int and record['compiler_exit_code'] == 0
    assert record['reused_synthesis'] is False
    assert record['working_source_changed_after_snapshot'] is False
    for name, digest in {**record['source_sha256'], **record['evidence_sha256']}.items():
        path = (build/name).resolve()
        assert path.is_relative_to(build) and sha(path) == digest, name
    driver = build/'EXECUTED_BUILD_DRIVER.py'
    assert sha(driver) == record['driver_sha256']
    assert sha(build/'build_native.tcl') == record['build_tcl_sha256']
    assert sha(build/'synthesis_gate.py') == record['synthesis_gate_sha256']
    assert sha(build/'check_native.py') == record['checker_sha256']
    assert sha(build/'read_active_json.py') == record['json_reader_sha256']
    sys.path.insert(0, str(build))
    spec = importlib.util.spec_from_file_location('frozen_native_audit', driver)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ROOT = Path(__file__).resolve().parents[1]
    hashes, metadata = module.source_inputs(build/record['project_file'])
    assert hashes == record['source_sha256'] and metadata == record['project_metadata']
    expected = module.native_tcl(record['project_stem'], record['top'], record['usercode_hex'],
                               Path(record['python_path']), record['options'], False)
    assert (build/'build_native.tcl').read_text('ascii') == expected
    stem = record['project_stem']
    pnr = build/'impl/pnr'
    cmd = (pnr/'cmd.do').read_text(errors='replace')
    # Original absolute command arguments remain untouched. Check them against
    # their recorded original build; the local files are bound above by SHA256.
    original = Path(record['build_directory'])
    for flag, relative in {'-d': f'impl/gwsynthesis/{stem}.vg',
                           '-cst': metadata['cst'], '-sdc': metadata['sdc']}.items():
        match = re.search(r'^'+re.escape(flag)+r'\s+(.+?)\s*$', cmd, re.M)
        assert match and Path(match[1].strip('"')).resolve() == (original/relative).resolve(), flag
    for flag in ('-bit', '-tr', '-timing', '-cst_error'):
        assert re.search(r'^'+re.escape(flag)+r'\s*$', cmd, re.M), flag
    for flag, value in {'-p': 'GW2A-55C-PBGA484-8', '-pn': module.PART,
                        **{'-'+k: v for k, v in record['options'].items()
                           if k in ('place_option', 'route_option', 'route_maxfan')}}.items():
        assert re.search(r'^'+re.escape(flag)+r'\s+'+re.escape(str(value))+r'\s*$', cmd, re.M), flag
    frequency = re.search(r'^-global_freq\s+([\d.]+)\s*$', cmd, re.M)
    assert frequency and float(frequency[1]) == 50
    report = (pnr/f'{stem}.rpt.txt').read_text(errors='replace')
    messages = (pnr/f'{stem}.log').read_text(errors='replace')
    console = (build/'compile.log').read_text(errors='replace')
    module.check_native_messages(report, messages, console)
    assert 'Placement and routing completed' in messages and 'Bitstream generation completed' in messages
    unrouted = [line for line in (report+messages+console).splitlines() if re.search('unrout', line, re.I)]
    assert all(re.search(r'\b0\s+unrout', line, re.I) for line in unrouted)
    metrics = module.diagnostics(build, stem)
    module.validate_timing(metrics)
    from synthesis_gate import syn_timing
    gate = json.loads((build/'SYNTHESIS_GATE.json').read_text('utf-8'))
    syn = syn_timing((build/f'impl/gwsynthesis/{stem}_syn.rpt.html').read_text(errors='replace'))
    assert gate['status'] == 'PASS' and all(gate.get(k) == v for k, v in syn.items())
    image = pnr/f'{stem}.fs'
    assert sha(image) == record['bitstream_sha256']
    with image.open(encoding='ascii', errors='replace') as handle:
        header = ''.join(handle.readline() for _ in range(32))
    assert f'//Part Number: {module.PART}' in header
    assert '//Device-package: GW2A-55C-PBGA484' in header
    code = re.search(r'//UserCode:\s*0x([0-9a-fA-F]+)', header)
    assert code and int(code[1], 16) == int(record['usercode_hex'], 16) == 0x41444231
    print(json.dumps(dict(recheck='PASS', accepted=True, qualified=True,
                         bitstream_sha256=sha(image), final_fmax_mhz=metrics['final_fmax_mhz'],
                         evidence_relocated=True, immutable_original_paths_verified=True,
                         hardware_actions=0)))


if __name__ == '__main__':
    main()
