"""Build a local-only, bitstream-free source catalog from explicit inputs."""
from pathlib import Path, PurePosixPath
import collections
import hashlib
import json
import shutil
import stat
import sys
import zipfile

OUT = Path(__file__).resolve().parents[2]
CAT = OUT / '00_分类说明'
WORKSPACE = OUT.parents[1]
ARCHIVES = json.loads((CAT / 'input_archives.json').read_text(encoding='utf-8'))
C6 = WORKSPACE / 'J280/C6_Presentation_Delivery_20261030/J280_C6_Complete_With_Figures_20261030.zip'
SEQ = WORKSPACE / 'J280/V5_G_Regression_20261102/sequential_motion'
CATEGORIES = {'ADB1': '01_平衡与手势控制', 'R28': '02_六位置控制',
              'R28_Bluetooth': '03_电脑蓝牙端', 'DS13': '04_现场手绘',
              'C6': '05_GOWIN画字与真实波形/画字控制与波形绘制', 'HOST': '06_上位机源码'}
HOST_FIXTURES = {'backend/fixtures/adb1_fastp/diagnostic.bin', 'backend/fixtures/adb1_fastp/extension.bin', 'backend/fixtures/adb1_fastp/telemetry.bin', 'backend/fixtures/v4_usb_ble/usb_wire_frames.bin', 'backend/fixtures/v4_usb_ble/wire_frames.bin'}
BITSTREAM = {'.fs', '.bit', '.bin', '.svf', '.xsvf', '.jed', '.jic', '.sof', '.pof', '.rbt'}
PACKED = {'.zip', '.rar', '.7z', '.tar', '.gz', '.bz2', '.xz', '.tgz'}
COMPILED = {'.exe', '.dll', '.pyd', '.pyc', '.pyo', '.obj', '.o', '.lib', '.a', '.pdb', '.mexw64', '.db', '.dbs', '.dat', '.bak'}
ALLOWED = set(('.vue .js .mjs .css .step .glb .patch .java .typed .pywinrt .py .pyi .v .vh .sv .svh .c .h .cpp .tcl .m .ps1 .cmd .bat .do .sdc .cst .gprj '
               '.json .yaml .yml .xml .ini .toml .cfg .conf .txt .md .rst .csv .tsv .html .htm .log .rpt '
               '.f .lst .mem .hex .rom .slx .mat .wlf .fig .png .jpg .jpeg .pdf .svg .ps .npz .npy .ipx .ipc .qsf .sdcx').split())
SKIP_PARTS = {'runtime', '__pycache__', '.git', '.pytest_cache', 'node_modules', '_internal'}
rows = []
excluded = []
inputs = []

def digest(file):
    h = hashlib.sha256()
    with Path(file).open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            h.update(block)
    return h.hexdigest()

def reason(name):
    p = PurePosixPath(name.replace('\\', '/'))
    parts = [x.lower() for x in p.parts]
    ext = p.suffix.lower()
    if ext in BITSTREAM:
        return 'fs_extension_removed' if ext == '.fs' else 'binary_capture_or_bitstream_image_removed'
    if any(x in SKIP_PARTS for x in parts):
        return 'bundled_runtime_or_cache_removed'
    if any(x == '.env' or x.startswith('.env.') or x in ('credentials', 'credentials.json', 'secrets.json', 'id_rsa', 'id_ed25519') for x in parts):
        return 'credential_filename_not_read_or_copied'
    if ext in {'.pem', '.key', '.p12', '.pfx'}:
        return 'credential_filename_not_read_or_copied'
    if ext in PACKED:
        return 'nested_archive_removed'
    if ext in COMPILED:
        return 'compiled_artifact_removed'
    if '/work/_' in '/' + '/'.join(parts) or any(x in ('_info', '_lib1_0', '_lib1_0.qdb') for x in parts):
        return 'simulator_cache_removed'
    if ext not in ALLOWED and p.name.lower() not in ('license', 'copying', 'readme', 'notice', '.gitignore', '.gitattributes'):
        return 'non_source_artifact_removed'
    return None

def safe_destination(category, relative):
    p = PurePosixPath(relative.replace('\\', '/'))
    if p.is_absolute() or '..' in p.parts or any(':' in x for x in p.parts):
        raise ValueError('Unsafe archive path')
    target = OUT / category / Path(*p.parts)
    if not target.resolve().is_relative_to(OUT.resolve()):
        raise ValueError('Destination outside collection')
    if target.exists():
        raise FileExistsError(str(target))
    target.parent.mkdir(parents=True, exist_ok=True)
    return target

def add_zip(ident, archive, strip_root):
    archive = Path(archive)
    inputs.append({'id': ident, 'path': str(archive), 'size': archive.stat().st_size, 'sha256': digest(archive)})
    with zipfile.ZipFile(archive) as z:
        for item in z.infolist():
            if item.is_dir():
                continue
            member = item.filename.replace('\\', '/')
            rel = member.split('/', 1)[1] if strip_root else member
            why = reason(rel)
            if ident == 'HOST' and PurePosixPath(rel).suffix.lower() in {'.txt', '.md'}:
                why = 'user_requested_document_removal'
            elif ident == 'HOST' and rel in HOST_FIXTURES:
                why = None
            if stat.S_ISLNK(item.external_attr >> 16):
                why = 'archive_symlink_removed'
            if why:
                excluded.append({'input': ident, 'member': member, 'reason': why, 'bytes': item.file_size})
                continue
            data = z.read(item)
            dest = safe_destination(CATEGORIES[ident], rel)
            dest.write_bytes(data)
            rows.append({'path': dest.relative_to(OUT).as_posix(), 'source_kind': 'zip',
                         'input': ident, 'source': str(archive), 'member': member,
                         'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()})

for item in ARCHIVES:
    add_zip(item['id'], item['path'], True)
add_zip('C6', C6, False)

# Copy only the actual-coordinate chain, not the later glyph presentation layer.
seq_files = [
    'host/measured_sequence.py', 'work/host/measured_sequence.py', 'work/host/sequence_viewer.py',
    'work/host/draw_trajectory.py', 'work/host/trajectory_settings.py',
    'tools/build_native_replay.py', 'tools/export_native.m', 'tools/prepare_native.py',
    'tests/test_measured_sequence.py', 'tests/test_sequence_viewer.py', 'cosim/run.py',
]
for subtree in ['plans', 'cosim/native', 'cosim/model_sources', 'cosim/cases/sequence_r03', 'results']:
    for file in (SEQ / subtree).rglob('*'):
        if file.is_file():
            rel = file.relative_to(SEQ).as_posix()
            if '/native/work/' in '/' + rel or '/first_viewport/' in '/' + rel:
                excluded.append({'input': 'SEQUENTIAL', 'member': rel, 'reason': 'simulator_cache_or_old_viewport_removed', 'bytes': file.stat().st_size})
            else:
                seq_files.append(rel)
for rel in sorted(set(seq_files)):
    source = SEQ / rel
    why = reason(rel)
    if why:
        excluded.append({'input': 'SEQUENTIAL', 'member': rel, 'reason': why, 'bytes': source.stat().st_size})
        continue
    if source.is_symlink():
        raise ValueError('Unexpected local symlink')
    before = digest(source)
    dest = safe_destination('05_GOWIN画字与真实波形/原始坐标采集与联合仿真', rel)
    shutil.copyfile(source, dest)
    if digest(source) != before or digest(dest) != before:
        raise ValueError('Local source changed during copy')
    rows.append({'path': dest.relative_to(OUT).as_posix(), 'source_kind': 'local', 'input': 'SEQUENTIAL',
                 'source': str(source), 'bytes': dest.stat().st_size, 'sha256': before})

with zipfile.ZipFile(ARCHIVES[1]['path']) as a, zipfile.ZipFile(ARCHIVES[2]['path']) as b:
    def pc_map(z):
        return {i.filename.split('/PC_Bluetooth/', 1)[1]: hashlib.sha256(z.read(i)).hexdigest()
                for i in z.infolist() if not i.is_dir() and '/PC_Bluetooth/' in i.filename}
    aa, bb = pc_map(a), pc_map(b)
    duplicates = {'complete_member_count': len(aa), 'pc_only_member_count': len(bb), 'all_pc_members_byte_identical': aa == bb,
                  'different': sorted(n for n in aa.keys() & bb.keys() if aa[n] != bb[n]),
                  'only_complete': sorted(aa.keys()-bb.keys()), 'only_pc': sorted(bb.keys()-aa.keys())}

def save(name, value):
    (CAT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

save('source_manifest.json', rows)
save('excluded_files.json', excluded)
save('input_hashes.json', inputs)
save('Bluetooth_Duplicate_Comparison.json', duplicates)
summary = {'status': 'COLLECTED_LOCAL_ONLY', 'files': len(rows), 'bytes': sum(r['bytes'] for r in rows),
           'categories': dict(collections.Counter(r['path'].split('/')[0] for r in rows)),
           'excluded_reasons': dict(collections.Counter(r['reason'] for r in excluded)),
           'uploaded': False, 'committed': False, 'hardware_actions': 0,
           'current_lettering_status': 'Successful lettering with good results, owner confirmation 2026-11-04'}
save('collection_summary.json', summary)
print(json.dumps(summary, ensure_ascii=False))
