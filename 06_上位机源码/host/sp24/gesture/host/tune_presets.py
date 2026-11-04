"""Explicit stopped-only presets; no motion or E qualification is inferred."""
import json
from pathlib import Path
from tune_parameters import EDITABLE, validate

PRESET_FILES = ('field_baseline.json', 'damping_d56.json', 'slow_i2.json', 'field_swing70_230.json',
                'angle_d80.json', 'angle_i780.json')
PRESET_ROOT = Path(__file__).resolve().parents[2]/'presets'


def validate_preset(value):
    if not isinstance(value, dict) or value.get('schema') not in (
            'J280.SP24.fixed-preset.v1', 'J280.SP24.swing-preset.v1'):
        raise ValueError('不是本包支持的参数预设')
    if value.get('firmware_uid_hex') != '53503234' or value.get('control_schema_hex') != '53505424':
        raise ValueError('预设固件身份不匹配')
    params = value.get('parameters')
    if value.get('no_RUN_ZERO_or_profile_commit') is not True or not isinstance(params, dict) or not params:
        raise ValueError('预设必须声明无自动运行、标零或配置提交')
    for name, number in params.items():
        if name not in EDITABLE or name.startswith(('error_', 'delta_', 'kp_', 'kd_')) or name in (
                'adaptive_profile', 'adaptive_integral') or (name == 'adaptive_mode' and number != 0):
            raise ValueError('预设不允许开启或提交自适应：'+name)
        validate(name, number)
    expected = value.get('expected_readonly', {})
    if not isinstance(expected, dict) or set(expected)-{'encoder_mode','position_gamma'}:
        raise ValueError('未知只读预设前提')
    if any(type(number) is not int for number in expected.values()):
        raise ValueError('只读预设前提必须是整数')
    return value


def load_presets():
    return [validate_preset(json.loads((PRESET_ROOT/name).read_text(encoding='utf-8-sig')))
            for name in PRESET_FILES]
