"""Bind a new input to the unchanged native RTL/conditional mechanical model."""
from pathlib import Path
import hashlib
import json
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).absolute().parents[1]
BASE = ROOT.parent / 'balance_port'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    plan_path = Path(sys.argv[1]).absolute()
    plan = json.loads(plan_path.read_text(encoding='utf-8'))
    sys.path.insert(0, str(BASE / 'work/host'))
    from draw_trajectory import validate, checksum
    validate(plan['points'])
    assert plan['tracking_mode'] == 1
    old = BASE / 'cosim/cases/G_sensor_fast_corrected100'
    frozen = json.loads((old / 'MODEL_FREEZE.json').read_text(encoding='utf-8'))
    out = ROOT / 'cosim'
    case_name=sys.argv[2] if len(sys.argv)>2 else 'sequence_r01'
    assert re.fullmatch(r'[A-Za-z0-9_]+',case_name)
    assert not (out/'cases'/case_name).exists(), 'Never overwrite a case'
    native = out / 'native'
    compiled=(native/'work').is_dir()
    native.mkdir(parents=True,exist_ok=True)
    for name, digest in frozen['native_files'].items():
        source = BASE / 'cosim/native' / name
        assert sha(source) == digest, name
        if compiled:
            assert sha(native/name)==digest,name
        else:
            shutil.copyfile(source, native / name)
    (native / 'modelsim.ini').write_text('[Library]\nwork = work\nothers = $MODEL_TECH/../modelsim.ini\n', encoding='ascii')
    for exe, args in [('vlib', ['work']), ('vlog', ['-modelsimini', 'modelsim.ini', '-sv', '+incdir+.',
                       *[p.name for p in native.iterdir() if p.suffix in ('.v', '.sv')]])]:
        if compiled:
            continue
        with (native / (exe + '.log')).open('wb') as log:
            result = subprocess.run(['E:/Moildesim/win64/' + exe + '.exe', *args], cwd=native,
                                    stdout=log, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW)
        assert result.returncode == 0, exe
    case = out / 'cases' / case_name
    case.mkdir(parents=True)
    snapshot = case / 'native_snapshot'
    snapshot.mkdir()
    for name in frozen['native_files']:
        shutil.copyfile(native / name, snapshot / name)
    for name, digest in frozen['files'].items():
        source = old / name
        if source.suffix == '.m' or name in ('case_init.hex', 'pid_state.hex', 'cosim_setup.tcl',
                                             'j280_coupled_interval_mex.mexw64'):
            assert sha(source) == digest, name
            shutil.copyfile(source, case / name)
    shutil.copyfile(plan_path, case / 'TRIAL_INPUT.json')
    (case / 'program.hex').write_text(''.join(f'{p & 65535:04x}\n' for p in plan['points']), encoding='ascii')
    (case / 'modelsim.ini').write_text('[Library]\nwork = ../../native/work\nothers = $MODEL_TECH/../modelsim.ini\n', encoding='ascii')
    command = (old / 'start_case.do').read_text(encoding='ascii')
    command = re.sub(r'-gPOINTS=\d+', '-gPOINTS=' + str(len(plan['points'])), command)
    command = re.sub(r"-gCHECKSUM=32'h[0-9A-Fa-f]+", f"-gCHECKSUM=32'h{checksum(plan['points'],1):08X}", command)
    command = re.sub(r'-socket \d+', '-socket 4869', command)
    (case / 'start_case.do').write_text(command, encoding='ascii')
    freeze = {key: value for key, value in frozen.items() if key not in ('files', 'model_sources')}
    duration = (len(plan['points']) - 3) * .2 + 20
    freeze.update(case_name=case_name+'_sensor_conditional', duration_s=duration, port=4869,
                  noise_path=(BASE / 'cosim/common/ADC_NOISE_INPUT.mat').as_posix(),
                  input_plan_sha256=sha(plan_path), native_directory='native',
                  source_profile='Unchanged R04 fast-P RTL and unchanged conditional sensor_271 plant',
                  physical_qualification=False, hardware_actions=0)
    run = (case / 'run_case.m').read_text(encoding='utf-8')
    run = run.replace("'complete_G_and_original_tail',~isempty(wave)&&tab.time_s(end)-tab.time_s(wave)>=99.995",
                      f"'complete_sequence_and_tail',~isempty(wave)&&tab.time_s(end)-tab.time_s(wave)>={duration-.01:.6f}")
    (case / 'run_case.m').write_text(run, encoding='utf-8')
    freeze['files'] = {p.name: sha(p) for p in case.iterdir() if p.is_file()}
    model_dir=out/'model_sources'
    model_dir.mkdir(exist_ok=True)
    freeze['model_sources'] = {}
    for name, digest in frozen['model_sources'].items():
        source=BASE/name
        assert sha(source)==digest, name
        target=model_dir/source.name
        shutil.copyfile(source,target)
        freeze['model_sources'][target.relative_to(ROOT).as_posix()]=digest
    freeze['baseline_freeze_sha256'] = sha(old / 'MODEL_FREEZE.json')
    (case / 'MODEL_FREEZE.json').write_text(json.dumps(freeze, indent=2), encoding='utf-8')
    # Same owned-process runner and exact input/native freeze checks as the baseline.
    shutil.copyfile(BASE / 'cosim/run.py', out / 'run.py')
    print(json.dumps({'prepared': str(case), 'duration_s': duration,
                      'native_source_files': len(freeze['native_files']),
                      'controller_and_mechanical_parameters_changed': False}))


if __name__ == '__main__':
    main()
