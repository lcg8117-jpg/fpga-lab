"""Persist offline verification with exact input hashes; no serial operations."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time


def main():
    host = Path(__file__).resolve().parent
    root = host.parent
    output = root/'reports/host'
    output.mkdir(parents=True, exist_ok=True)
    inputs = list(host.glob('*.py')) + list(host.glob('*.ps1')) + [host/'HOST_GUIDE.md', root/'API-Contract.md'] + list((root.parent/'presets').glob('*.json'))
    hashes = {(p.relative_to(root) if p.is_relative_to(root) else Path('..')/p.relative_to(root.parent)).as_posix():
              hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}
    checks = [('unit_and_loopback', [sys.executable, '-X', 'utf8', '-m', 'unittest', 'test_tune_host', 'test_adaptive_profile', 'test_sp23', 'test_precise_telemetry', '-v']),
              ('offline_tk', [sys.executable, '-X', 'utf8', 'check_offline_ui.py']),
              ('waveform_render', [sys.executable, '-X', 'utf8', 'render_wave_evidence.py']),
              ('syntax', [sys.executable, '-X', 'utf8', '-m', 'py_compile', *[p.name for p in host.glob('*.py')]])]
    records = []
    for name, command in checks:
        start = time.monotonic()
        result = subprocess.run(command, cwd=host, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=45)
        raw = f'EXIT={result.returncode}\n'+result.stdout+result.stderr+f'\nEXIT_CODE={result.returncode}\n'
        passed = result.returncode == 0
        raw += ('PASS' if passed else 'FAIL')+' '+name+'\n'
        log = output/(name+'.log')
        log.write_text(raw, encoding='utf-8')
        records.append(dict(name=name,status='PASS' if passed else 'FAIL', seconds=round(time.monotonic()-start,3),
                            proof=('PASS ' if passed else 'FAIL ')+name, log=log.name))
        print(f'{name}: '+('PASS' if passed else 'FAIL'), flush=True)
    unchanged = all(hashlib.sha256((root/p).read_bytes()).hexdigest() == h for p,h in hashes.items())
    status = 'PASS' if unchanged and all(r['status']=='PASS' for r in records) else 'FAIL'
    (output/'verification.json').write_text(json.dumps(dict(status=status, tests=records,
        source_sha256=hashes, source_unchanged_during_test=unchanged, hardware_opened=False), ensure_ascii=False, indent=2), encoding='utf-8')
    print(status+' Host verification; no hardware opened')
    return 0 if status == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
