"""Independently verify the local collection against the unchanged inputs."""
from contextlib import ExitStack
from pathlib import Path
import ast
import collections
import csv
import hashlib
import json
import math
import re
import sys
import tokenize
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[2]
CAT = ROOT / '00_分类说明'
FORBIDDEN = {'.fs', '.bit', '.bin', '.svf', '.xsvf', '.jed', '.jic', '.sof', '.pof', '.rbt',
             '.zip', '.rar', '.7z', '.tar', '.gz', '.bz2', '.xz', '.tgz', '.exe', '.dll', '.pyd', '.pyc', '.pyo', '.mexw64'}

def sha(file):
    h = hashlib.sha256()
    with Path(file).open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            h.update(block)
    return h.hexdigest()

def load(file):
    return json.loads(Path(file).read_text(encoding='utf-8-sig'))

manifest = load(CAT / 'source_manifest.json')
inputs = load(CAT / 'input_hashes.json')
excluded = load(CAT / 'excluded_files.json')
errors = []
verified = 0
with ExitStack() as stack:
    archives = {item['path']: stack.enter_context(zipfile.ZipFile(item['path'])) for item in inputs}
    for item in inputs:
        if sha(item['path']) != item['sha256']:
            errors.append({'input_changed': item['id']})
    for row in manifest:
        dest = ROOT / row['path']
        if not dest.is_file() or dest.stat().st_size != row['bytes'] or sha(dest) != row['sha256']:
            errors.append({'destination_mismatch': row['path']})
            continue
        source_sha = hashlib.sha256(archives[row['source']].read(row['member'])).hexdigest() if row['source_kind'] == 'zip' else sha(row['source'])
        if source_sha != row['sha256']:
            errors.append({'source_mismatch': row['path']})
        else:
            verified += 1

files = [p for p in ROOT.rglob('*') if p.is_file()]
forbidden = [p.relative_to(ROOT).as_posix() for p in files if p.suffix.lower() in FORBIDDEN]
errors.extend({'forbidden_file': name} for name in forbidden)
containers = []
for p in files:
    if p.suffix.lower() in {'.slx', '.npz'}:
        with zipfile.ZipFile(p) as z:
            bad = [n for n in z.namelist() if Path(n).suffix.lower() in FORBIDDEN]
            containers.append({'path': p.relative_to(ROOT).as_posix(), 'members': len(z.namelist()), 'forbidden_members': bad})
            errors.extend({'forbidden_container_member': n, 'container': p.relative_to(ROOT).as_posix()} for n in bad)

projects = []
for p in files:
    if p.suffix.lower() != '.gprj':
        continue
    active = [e.get('path') for e in ET.parse(p).findall('.//File') if e.get('enable', '1') != '0']
    missing = [name for name in active if not (p.parent / name.replace('\\', '/')).is_file()]
    projects.append({'path': p.relative_to(ROOT).as_posix(), 'enabled_inputs': len(active), 'missing_inputs': missing})
    errors.extend({'missing_project_input': name, 'project': p.relative_to(ROOT).as_posix()} for name in missing)

parsed = 0
syntax_errors = []
for p in files:
    if p.suffix.lower() == '.py':
        try:
            with tokenize.open(p) as stream:
                ast.parse(stream.read(), filename=p.name)
            parsed += 1
        except (SyntaxError, UnicodeError) as exc:
            syntax_errors.append({'path': p.relative_to(ROOT).as_posix(), 'line': getattr(exc, 'lineno', None), 'type': type(exc).__name__})
errors.extend({'syntax_error': row} for row in syntax_errors)

broken_links = []
for p in [ROOT / 'README.md', CAT / 'Tech-Spec.md', CAT / 'Waveform_Index.md']:
    text = p.read_text(encoding='utf-8')
    for link in re.findall(r'\[[^\]]+\]\(([^)]+)\)', text):
        if link.startswith(('https://', 'http://', '#')):
            continue
        target = link.split('#', 1)[0].strip('<>')
        if target and not (p.parent / target).exists() and (p.parent / target).resolve() != (CAT / 'VERIFICATION.json').resolve():
            broken_links.append({'file': p.relative_to(ROOT).as_posix(), 'link': link})
errors.extend({'broken_link': row} for row in broken_links)

# Recompute the native plotted coordinates directly from timestamped feedback.
seq = ROOT / '05_GOWIN画字与真实波形/原始坐标采集与联合仿真'
metric = load(seq / 'results/NATIVE_METRICS.json')
onset_ms = round(metric['onset_s'] * 1000)
window_ms = round(metric['window_s'] * 1000)
enc = {}
with (seq / 'cosim/cases/sequence_r03/FULL_1KHZ.csv').open(encoding='utf-8', newline='') as stream:
    for r in csv.DictReader(stream):
        enc[round(float(r['time_s']) * 1000)] = int(float(r['frame_actual']))
max_x_error = 0
max_y_error = 0.0
xy_count = 0
with (seq / 'results/NATIVE_MEASURED_XY.csv').open(encoding='utf-8', newline='') as stream:
    for r in csv.DictReader(stream):
        ms = onset_ms + int(r['relative_ms'])
        expected_x = enc[ms] - metric['actual_origin_count']
        expected_y = (enc[ms] - enc[ms-window_ms]) * 2*math.pi / (1040 * metric['window_s'])
        max_x_error = max(max_x_error, abs(float(r['actual_x_count']) - expected_x))
        max_y_error = max(max_y_error, abs(float(r['actual_y_rad_s']) - expected_y))
        xy_count += 1
if max_x_error != 0 or max_y_error > 1e-12:
    errors.append({'native_waveform_data_mismatch': [max_x_error, max_y_error]})

c6 = ROOT / '05_GOWIN画字与真实波形/画字控制与波形绘制'
provenance = load(c6 / 'Source/evidence/C4_GOWIN_PROVENANCE.json')
physical_fixture_sha = sha(c6 / 'Source/evidence/C4_GOWIN_saved_rows.npz')
if physical_fixture_sha != provenance['fixture_sha256']:
    errors.append({'physical_fixture_sha_mismatch': True})

mapped_names = {'sequential_glyph.py', 'sequential_glyph_viewer.py', 'glyph_visualization.py', 'word_visualization.py'}
mapped_copies = [p.relative_to(ROOT).as_posix() for p in (ROOT / '05_GOWIN画字与真实波形').rglob('*.py') if p.name in mapped_names]
errors.extend({'unexpected_mapped_presentation_copy': p} for p in mapped_copies)
report = {'status': 'PASS' if not errors else 'CHECK_REQUIRED', 'source_files_verified': verified,
          'original_archives_unchanged': len(inputs), 'original_local_sources_checked': sum(r['source_kind'] == 'local' for r in manifest),
          'forbidden_file_count': len(forbidden), 'fs_file_count': sum(p.suffix.lower() == '.fs' for p in files),
          'removed_fs_entries': sum(r['reason'] == 'fs_extension_removed' for r in excluded),
          'checked_model_data_containers': containers, 'gowin_projects': projects,
          'python_files_parsed': parsed, 'syntax_errors': syntax_errors, 'broken_generated_links': broken_links,
          'native_coordinate_recheck': {'points': xy_count, 'maximum_x_error_count': max_x_error,
                                      'maximum_y_error_rad_s': max_y_error, 'source_kind': 'native_RTL_Simulink_feedback'},
          'physical_log_fixture_sha_matches_original_provenance': physical_fixture_sha == provenance['fixture_sha256'],
          'mapped_presentation_copies': mapped_copies, 'current_lettering_status': 'Successful with good results, owner confirmation 2026-11-04',
          'uploaded': False, 'committed': False, 'hardware_actions': 0, 'errors': errors}
(CAT / 'VERIFICATION.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps({k: v for k, v in report.items() if k not in ('checked_model_data_containers', 'gowin_projects', 'errors')}, ensure_ascii=False))
print(json.dumps({'projects': projects, 'model_data_containers': len(containers), 'errors': errors}, ensure_ascii=False))
if errors:
    raise SystemExit(1)
