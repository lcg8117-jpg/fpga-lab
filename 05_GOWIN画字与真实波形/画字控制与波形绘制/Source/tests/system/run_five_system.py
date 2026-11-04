"""Full actual50MHz/2Mbaud quiet-chain integration with declared seam inputs."""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import xml.etree.ElementTree as ET


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--work-root', type=Path, required=True)
    p.add_argument('--modelsim', type=Path, default=Path('E:/Moildesim/win64'))
    p.add_argument('--variants', nargs='+', type=int, default=[2,4,5])
    p.add_argument('--report',default='reports/FIVE_SYSTEM.json')
    a = p.parse_args()
    root = Path(__file__).resolve().parents[2]
    base = a.work_root.resolve() / datetime.now().strftime('run_%Y%m%d_%H%M%S_%f')
    if not str(base).isascii():
        raise SystemExit('ASCII working directory required')
    if len(set(a.variants)) != len(a.variants) or any(v not in (2,4,5) for v in a.variants):
        raise SystemExit('Select unique variants2/4/5')
    base.mkdir(parents=True, exist_ok=False)
    evidence = root / 'tests/system/five_evidence'
    evidence.mkdir(exist_ok=True)
    hashes, commands, lines, variant_results = {}, [], [], []

    def execute(work, record, name, argv, timeout=480):
        r = subprocess.run(argv, cwd=work, capture_output=True, text=True, errors='replace', timeout=timeout, creationflags=subprocess.CREATE_NO_WINDOW)
        output = r.stdout + r.stderr
        (work / (name + '.log')).write_text(output, encoding='utf-8')
        command = {'name': name, 'argv': [str(s) for s in argv], 'returncode': r.returncode,
                   'working_directory': str(work), 'variant': record['variant']}
        commands.append(command)
        record['commands'].append(command)
        if r.returncode or '** Fatal:' in output or '** Error:' in output:
            raise RuntimeError(name + '\n' + output[-7000:])
        return output

    try:
        for variant in a.variants:
            # Compile only this actual deliverable's closure into its own
            # library. Canonical RTL must not silently replace a frozen input.
            project = (root / ('V' + str(variant)) / 'project').resolve()
            gprj = project / ('forest_d1_v' + str(variant) + '.gprj')
            files, verilog, enabled = [gprj], [], []
            for item in ET.parse(gprj).findall('.//File'):
                if item.get('enable') != '1':
                    continue
                file = (project / item.get('path')).resolve()
                file.relative_to(project)
                file_type = item.get('type')
                if file_type not in ('file.verilog', 'file.sdc', 'file.cst', 'file.gsc'):
                    raise RuntimeError('Unsupported enabled GPRJ input type: ' + str(file_type))
                files.append(file)
                enabled.append({'path': file.relative_to(root).as_posix(), 'type': file_type,
                                'compiled_as_HDL': file_type == 'file.verilog'})
                if file_type == 'file.verilog':
                    verilog.append(file.name)
            files += list((project / 'src').glob('*.vh'))
            files += [root / 'tests/system/five_system_tb.sv', Path(__file__).resolve()]
            work = base / ('V' + str(variant))
            work.mkdir()
            variant_hashes = {}
            for file in files:
                destination = work / file.name
                if destination.exists():
                    raise RuntimeError('Duplicate basename in actual project closure: ' + file.name)
                shutil.copy2(file, destination)
                name = file.relative_to(root).as_posix()
                sha = hashlib.sha256(file.read_bytes()).hexdigest()
                if name in hashes and hashes[name] != sha:
                    raise RuntimeError('Shared test input changed between variants: ' + name)
                variant_hashes[name] = sha
                hashes[name] = sha
            record = {'variant': variant, 'accepted': False, 'input_sha256': variant_hashes,
                      'project': project.relative_to(root).as_posix(), 'enabled_project_inputs': enabled,
                      'source_selection': 'actual GPRJ enabled closure', 'canonical_RTL_used': False,
                      'non_HDL_inputs_compiled': False, 'work_directory': str(work),
                      'commands': [], 'results': []}
            variant_results.append(record)
            try:
                execute(work, record, 'vlib_' + str(variant), [a.modelsim / 'vlib.exe', 'work'])
                execute(work, record, 'compile_all_' + str(variant),
                        [a.modelsim / 'vlog.exe', '-sv', '+incdir+.'] + verilog + ['five_system_tb.sv'])
                output = execute(work, record, 'system_' + str(variant),
                                 [a.modelsim / 'vsim.exe', '-c', '-t', '1ns',
                                  '-gVARIANT=' + str(variant), 'work.five_system_tb', '-do',
                                  'onerror {quit -code 1}; onbreak {quit -code 1}; run -all; quit -code 0'])
                passed = [s for s in output.splitlines() if 'PASS system ' in s]
                if len(passed) != 1:
                    raise RuntimeError('No unique PASS marker\n' + output[-7000:])
                lines.extend(passed)
                record['results'] = passed
                record['accepted'] = True
                print('\n'.join(passed), flush=True)
            finally:
                stable = all(hashlib.sha256((root / name).read_bytes()).hexdigest() == sha
                             for name, sha in variant_hashes.items())
                record['sources_unchanged_during_run'] = stable
                record['accepted'] = record['accepted'] and stable
                receipt = base / ('SOURCE_V' + str(variant) + '.json')
                receipt.write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
                shutil.copy2(receipt, evidence / receipt.name)
                for file in work.glob('*.log'):
                    shutil.copy2(file, evidence / file.name)
    finally:
        stable = all(hashlib.sha256((root / name).read_bytes()).hexdigest() == sha for name, sha in hashes.items())
        result = {'schema': 2, 'hardware_accessed': False,
                  'accepted': len(lines) == len(a.variants) and stable and all(v['accepted'] for v in variant_results),
                  'input_sha256': hashes, 'commands': commands, 'results': lines,
                  'sources_unchanged_during_run': stable, 'work_directory': str(base),
                  'source_selection': 'per-variant actual GPRJ enabled closure', 'canonical_RTL_used': False,
                  'variant_sources': variant_results,
                  'variants': a.variants, 'clock_Hz': 50000000, 'baud': 2000000,
                  'ADC_Hz': 3125000, 'angle_period_us': 5000, 'position_period_us': 25000,
                  'watchdog_cycles': 25000000, 'startup_cycles': 100, 'zero_samples': 8,
                  'seams': ['V2 optical event_valid/code/ready wire forced briefly; optical transport tested separately',
                            'V4 swing start and STOP use actual UART commands, with no optical event seam',
                            'V5 manual ZERO/RUN/STOP, removed swing rejection and two fixed profiles and validated RAM upload/playback use actual UART while the balance owner stays active; no swing/optical seam',
                            'V2/V4 swing_finished wire forced once; real swing qualification tested separately'],
                  'swing_capable_variants': [v for v in a.variants if v in (1,2,4)],
                  'manual_only_variants': [v for v in a.variants if v == 5],
                  'plant_simulated': False}
        result['pin_route_assertions_per_clock'] = 4
        result['physical_estop_route_tested'] = True
        (base / 'RESULT.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
        shutil.copy2(base / 'RESULT.json', evidence / 'RESULT.json')
        (root / a.report).write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(str(base / 'RESULT.json'), flush=True)


if __name__ == '__main__':
    main()
