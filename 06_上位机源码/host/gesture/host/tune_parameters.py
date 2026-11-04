"""Typed UI/CLI parameter inventory matching API-Contract.md."""
PARAMETERS = [
    (0x00, 'forward_percent', '前向主动驱动占空比', '%', 49, 1, 90),
    (0x02, 'forward_ms', '前向主动驱动时间', 'ms', 100, 1, 1000),
    (0x01, 'reverse_percent', '反向主动驱动占空比', '%', 36, 1, 90),
    (0x05, 'reverse_ms', '反向主动驱动时间', 'ms', 220, 1, 1000),
    (0x03, 'qualify_ms', '下垂静止确认', 'ms', 60, 1, 1000),
    (0x04, 'wait_timeout_ms', '等待下垂最长时间', 'ms', 3000, 1, 10000),
    (0x06, 'coast_ms', '接管等待时间', 'ms', 1800, 20, 10000),
    (0x08, 'max_attempts', '每次RUN最多尝试', '次', 3, 1, 10),
    (0x09, 'retry_step_ms', '重试脉冲调整步长', 'ms', 10, 0, 50),
    (0x20, 'angle_kp', '角度P', '原始值', 0, 0, 65535),
    (0x21, 'angle_kd', '角度D', '原始值', 0, 0, 65535),
    (0x22, 'angle_ki', '角度I', '原始值', 0, 0, 65535),
    (0x23, 'position_kp', '位置P', '原始值', 0, 0, 65535),
    (0x24, 'position_kd', '位置D', '原始值', 0, 0, 65535),
    (0x25, 'position_ki', '位置I', '原始值', 0, 0, 65535),
    (0x26, 'ki_enable', '积分使能', '0/1', 0, 0, 1),
    (0x27, 'encoder_mode', '编码器模式（只读）', '0/1', 1, 0, 1),
    (0x28, 'angle_d_filter', '原有角度D滤波', 'Q12', 757, 0, 4096),
    (0x29, 'angle_window', '原有角度窗口', '点', 4, 1, 8),
    (0x2A, 'position_gamma', '位置D滤波（只读）', 'Q31', 578949194, 1, 0x80000000),
]
PARAMETERS += [(0x30, 'gesture_enable', '手势控制开关', '0关/1开', 0, 0, 1)]
BY_NAME = {p[1]: p for p in PARAMETERS}
BY_ID = {p[0]: p for p in PARAMETERS}
SWING_PARAMETERS = [p for p in PARAMETERS if p[0] < 0x20]
BALANCE_PARAMETERS = [p for p in PARAMETERS if p[0] >= 0x20]
EDITABLE = {p[1] for p in PARAMETERS if p[0] <= 0x26 or p[0] in (0x28, 0x29, 0x30)}
PID_FIELDS = [p[1] for p in PARAMETERS if 0x20 <= p[0] <= 0x26]
RESTORE_FIELDS = PID_FIELDS + ['angle_d_filter', 'angle_window']
HISTORICAL_PID = dict(angle_kp=900, angle_ki=870, angle_kd=90,
                      position_kp=112, position_ki=0, position_kd=48, ki_enable=1,
                      angle_d_filter=650, angle_window=5)


def validate(name, value):
    if name not in EDITABLE:
        raise ValueError('未知或只读参数')
    spec = BY_NAME[name]
    if type(value) is not int or not spec[5] <= value <= spec[6]:
        raise ValueError(f'{spec[2]}必须是{spec[5]}..{spec[6]}的整数')
    return spec
