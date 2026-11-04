"""Validate finite G/O RAM trials without transport or motion side effects."""
import hashlib
import json
import math
import re
from pathlib import Path

from draw_trajectory import FORMAT, checksum, clocked_curve, validate

PLAN_DIRECTORY = Path(__file__).resolve().parent / 'plans'
BASELINE_SHA256 = {
    'G': 'aac8e8c35a86f8e501783fac1e9d4baeaea2c7033029605c3c4127675259ba13',
    'O': '5d1ad8de5c1b32d4f56111fbd558d0c71e50991f0c9575e18808e38b9ac0277b',
}


def canonical_sha(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def _read(path):
    path = Path(path)
    if path.suffix.lower() != '.json' or path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError('仅允许4MiB以内的G/O试验JSON')
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:raise ValueError('JSON包含重复字段')
            result[key] = value
        return result
    raw = path.read_bytes()
    data = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=unique_object,
        parse_constant=lambda value: (_ for _ in ()).throw(ValueError('JSON含非有限值')))
    return data, hashlib.sha256(raw).hexdigest()


def validate_plan(data, *, nominal_target=None):
    """Keep nominal geometry fixed while independently checking command knots."""
    if not isinstance(data, dict):raise ValueError('试验计划必须为JSON对象')
    if (data.get('format') != FORMAT or type(data.get('sample_ms')) is not int or
        data['sample_ms'] != 100 or type(data.get('tracking_mode')) is not int or
        data['tracking_mode'] != 1 or data.get('bank', 0) != 0 or
        data.get('source_kind', 'custom_ram') != 'custom_ram'):
        raise ValueError('只允许bank0、tracking_mode1的G/O自定义RAM计划')
    points = validate(data.get('points'))
    meta = data.get('word_metadata');trial = data.get('trial_metadata')
    if not isinstance(meta, dict) or not isinstance(trial, dict):raise ValueError('缺少试验身份或时间元数据')
    word = meta.get('word');revision = trial.get('revision')
    if word not in ('G', 'O') or not isinstance(revision, str) or not re.fullmatch(r'R[0-9]{2,4}', revision):
        raise ValueError('仅支持G/O及明确R修订号')
    expected = dict(schema='V5GO1', trial_id=f'Trial_{word}_{revision}',
        firmware_hex='56354634', kit='IP570', counts_per_rev=1040,
        nominal_target_id=f'{word}_R01')
    if any(trial.get(k) != v for k, v in expected.items()):raise ValueError('试验身份、固件、套件或固定目标不匹配')
    if not isinstance(trial.get('compensation'), str) or not trial['compensation']:
        raise ValueError('必须声明输入修正方法')
    if revision == 'R01' and trial['compensation'] != 'uncompensated':raise ValueError('R01必须为未补偿基线')
    if (type(meta.get('checksum')) is not int or meta['checksum'] != checksum(points, 1) or
        type(meta.get('point_count')) is not int or meta['point_count'] != len(points) or
        type(meta.get('total_s')) not in (int, float) or
        not math.isclose(meta['total_s'], (len(points)-3)*.2, rel_tol=0, abs_tol=1e-10) or
        meta.get('tracking_mode') != 1 or meta.get('playback_sample_ms') != 200):
        raise ValueError('CRC、点数、模式或200ms实际段时长不匹配')
    times = tuple(meta.get(k) for k in ('word_start_ms', 'word_end_ms', 'window_ms'))
    if any(type(v) is not int or v % 5 for v in times):raise ValueError('试验时间必须按5ms控制拍对齐')
    start_ms, end_ms, window_ms = times
    q = clocked_curve(points, 1)
    if not 0 <= start_ms-window_ms < start_ms < end_ms < len(q)*5:
        raise ValueError('书写区间没有完整过去窗口或超出有限计划')
    # Preserve the original full-reference Q16 position and derivative guards.
    maxima = [0, 0, 0, 0];previous = velocity = acceleration = 0
    for value in q:
        step = value-previous;second = step-velocity;third = second-acceleration
        maxima = [max(a, abs(b)) for a, b in zip(maxima, (value, step, second, third))]
        previous, velocity, acceleration = value, step, second
    if (any(v > limit for v, limit in zip(maxima, (192*65536, 19663, 524, 13))) or
        (q[-1], velocity, acceleration) != (0, 0, 0)):
        raise ValueError('完整参考违反原位置、速度、加速度、jerk或终态限制')
    target = data.get('target_only_xy')
    if (not isinstance(target, list) or len(target) != (end_ms-start_ms)//5 or
        any(not isinstance(row, (list, tuple)) or len(row) != 2 or
            any(type(v) not in (int, float) or not math.isfinite(v) for v in row) for row in target)):
        raise ValueError('固定名义目标须与5ms书写区间一致且全为有限坐标')
    if nominal_target is not None and target != nominal_target:
        raise ValueError('名义G/O目标已变更；后续修订只能改输入节点')
    lag = window_ms//5
    preview = [(q[i]/65536, (q[i]-q[i-lag])/65536*2*math.pi/1040/(window_ms/1000))
        for i in range(start_ms//5, end_ms//5)]
    return dict(data, points=points, word_metadata=dict(meta), trial_metadata=dict(trial),
        command_reference_xy=preview, reference_Q16_maxima=maxima,
        nominal_target_sha256=canonical_sha(target))


def load_plan(path, *, plan_directory=PLAN_DIRECTORY):
    data, digest = _read(path)
    program = validate_plan(data)
    word = program['word_metadata']['word']
    baseline_data, baseline_digest = _read(Path(plan_directory)/f'Trial_{word}_R01.json')
    if baseline_digest != BASELINE_SHA256[word]:raise ValueError('R01固定基线文件已改变；请恢复交付原件')
    baseline = validate_plan(baseline_data)
    if baseline['trial_metadata']['revision'] != 'R01':raise ValueError('固定目标基线修订不匹配')
    for key in ('word_start_ms', 'word_end_ms', 'window_ms'):
        if program['word_metadata'][key] != baseline['word_metadata'][key]:
            raise ValueError('后续修订必须保留固定目标的书写时间与过去窗口')
    program = validate_plan(data, nominal_target=baseline['target_only_xy'])
    if program['trial_metadata']['revision'] == 'R01' and data != baseline_data:
        raise ValueError('修改输入必须使用新修订号；禁止覆盖R01身份')
    identity = dict(program['trial_metadata'], plan_sha256=digest,
        checksum=program['word_metadata']['checksum'], bank=0, tracking_mode=1, planner_direction=1,
        nominal_target_sha256=program['nominal_target_sha256'])
    program['word_metadata']['trial_identity'] = identity
    program['plan_sha256'] = digest
    return program
