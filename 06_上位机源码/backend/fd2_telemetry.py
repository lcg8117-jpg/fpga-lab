"""FD2 display adapter. Original Host owns all serial, command and logging work."""
import math
import re

from sp24_telemetry import raw_integer, safe_json


IDENTITIES = {1: ('46443231', '46443203', 2), 2: ('46443432', '46443402', 3),
              3: ('46443233', '46443203', 2), 4: ('46443434', '46443404', 4),
              5: ('46443435', '46443405', 5), 16: ('41444231', '41444203', 3)}
DELIVERY_PROFILES = {
    'ADB1_07': (16, '41444231', '41444203', 3),
    'ADB1_FASTP': (16, '41444231', '41445001', 3),
    'V4_R28': (4, '56345239', '56345208', 4),
    'V4_USB_BLE': (4, '5634523A', '56345208', 4),
    'G3_C1': (5, '56354733', '46443405', 5),
    'DS13': (5, '44533133', '44535002', 5),
}
V4_R28_PROFILES = frozenset(('V4_R28', 'V4_USB_BLE'))
ADB1_PROFILES = frozenset(('ADB1_FASTP', 'ADB1_07'))
CONTROL_CHANNELS = ['angle_deg', 'alpha_ref_deg', 'arm_angle_deg', 'arm_reference_deg',
                    'duty_actual_percent', 'angle_kp', 'angle_kd']
COUNT_SCALE = 360 / 1040
POSE_Q4_DEG_SCALE = 6921 / (16 * 2**20) * 180 / math.pi
FRESH_SECONDS = .5


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def fresh(age):
    return finite(age) and 0 <= age <= FRESH_SECONDS


def capabilities(variant):
    if isinstance(variant, str):
        if variant in DELIVERY_PROFILES:
            return dict(swing_up=variant in ADB1_PROFILES, optical_gesture=variant in ADB1_PROFILES,
                        adaptive=variant in ADB1_PROFILES, positions=variant in V4_R28_PROFILES, trajectories=False,
                        ble_commands=variant in V4_R28_PROFILES, g3_c1=variant == 'G3_C1',
                        live_draw=variant == 'DS13')
        variant = 16 if variant == 'ADB1' else int(variant[1:]) if variant.startswith('V') and variant[1:].isdigit() else None
    return dict(swing_up=variant in (1, 2, 4, 16), optical_gesture=variant in (1, 2, 16),
                adaptive=variant in (3, 16), positions=variant in (4, 5),
                trajectories=variant == 5, ble_commands=variant in (4, 5))


def identify(snapshot):
    variant = snapshot.get('variant')
    if snapshot.get('protocol') != 'FD2' or type(variant) is not int:
        raise ValueError('FD2 版本身份无效')
    if type(snapshot.get('wire_version')) is not int:
        raise ValueError('FD2 固件、协议版本或 schema 不匹配')
    identity = (variant, snapshot.get('user_code'), snapshot.get('schema'), snapshot['wire_version'])
    declared = snapshot.get('delivery_profile')
    if declared == 'ADB1_07':
        if identity != DELIVERY_PROFILES[declared]:
            raise ValueError('07 Host 声明与完整固件身份不匹配')
        return declared
    for name, candidate in DELIVERY_PROFILES.items():
        if name == 'ADB1_07':
            continue  # Same wire identity as historical ADB1; declaration is software provenance only.
        if identity == candidate:
            return name
    if variant not in IDENTITIES:
        raise ValueError('FD2 版本身份无效')
    uid, schema, wire = IDENTITIES[variant]
    if (snapshot.get('user_code') != uid or snapshot.get('schema') != schema or
            type(snapshot.get('wire_version')) is not int or snapshot['wire_version'] != wire):
        raise ValueError('FD2 固件、协议版本或 schema 不匹配')
    return 'ADB1' if variant == 16 else f'V{variant}'


def validate_action(body, snapshot):
    variant = snapshot['variant']
    profile = identify(snapshot)
    action = body.get('action')
    allowed = {'stop', 'estop', 'zero', 'balance', 'clear-fault', 'start-swing',
               'adapt-enable', 'adapt-disable', 'position', 'trajectory', 'hold',
               'set-gains', 'read-gains', 'stroke-begin', 'stroke-move', 'stroke-hold', 'stroke-end',
               'play-c1', 'cancel-c1'}
    if action not in allowed:
        raise ValueError('FD2 操作不受支持')
    rid = body.get('request_id')
    if not isinstance(rid, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', rid):
        raise ValueError('操作需要唯一 request_id')
    stroke = action.startswith('stroke-')
    keys = ({'action', 'request_id', 'session', 'stroke_id', 'seq'} |
            ({'target'} if action in ('stroke-begin', 'stroke-move') else set())) if stroke else (
            {'action', 'request_id', 'value'} if action in ('position', 'trajectory', 'set-gains') else {'action', 'request_id'})
    if profile == 'V4_USB_BLE' and action not in ('stop', 'estop'):
        keys.add('session')
        if not isinstance(body.get('session'), str) or not body['session'] or body['session'] != snapshot.get('session'):
            raise ValueError('网页显示的会话已变化，请重新确认状态')
    if stroke and 'issued_ms' in body: keys.add('issued_ms')
    if set(body) != keys or (action in ('position', 'trajectory') and
                            (type(body.get('value')) is not int or not 1 <= body['value'] <= 6)):
        raise ValueError('操作字段或位置／轨迹编号无效')
    if action in ('set-gains', 'read-gains') and profile not in V4_R28_PROFILES | ADB1_PROFILES:
        raise ValueError('当前固件不支持网页 PID 操作')
    if action in ('play-c1', 'cancel-c1') and profile != 'G3_C1':
        raise ValueError('当前固件不支持 C1 轨迹')
    if action == 'set-gains':
        values = body['value']
        if (not isinstance(values, list) or len(values) != 6 or
                any(not finite(v) or v < 0 or v > 10000 for v in values) or
                (profile in V4_R28_PROFILES and (values[1] != 0 or values[4] != 0))):
            raise ValueError('PID 需要六个合法参数，V4 R28 积分必须为零')
    if stroke:
        token = body.get('stroke_id')
        seq = body.get('seq')
        if (profile != 'DS13' or body.get('session') != snapshot.get('session') or
                not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', token) or
                type(seq) is not int or not 0 < seq <= 0xffffffff or
                ('issued_ms' in body and (type(body['issued_ms']) is not int or not 0 < body['issued_ms'] < 10**16)) or
                (action in ('stroke-begin', 'stroke-move') and
                 (type(body.get('target')) is not int or not -192 <= body['target'] <= 192))):
            raise ValueError('手绘会话、序号或目标无效')
    replay = snapshot.get('replay') or {}
    if replay.get('active'):
        raise ValueError('离线回放禁止发送硬件命令')
    if not snapshot.get('connected'):
        raise ValueError('请在配套 Host 中明确连接设备')
    if action in ('stop', 'estop'):
        return body
    if not snapshot.get('synced') or not fresh(snapshot.get('status_age_s')):
        raise ValueError('身份未核验或状态超过500ms，禁止控制')
    host = snapshot.get('host') or {}
    if host.get('error') or host.get('log_error'):
        raise ValueError('Host 错误或日志异常，禁止新的控制请求')
    caps = capabilities(profile)
    requirement = {'start-swing': 'swing_up', 'adapt-enable': 'adaptive', 'adapt-disable': 'adaptive',
                   'position': 'positions', 'trajectory': 'trajectories', 'hold': 'positions'}.get(action)
    if requirement and not caps[requirement]:
        raise ValueError('当前固件不支持此功能')
    if action in ('position', 'trajectory', 'play-c1') and not fresh(snapshot.get('control_age_s')):
        raise ValueError('控制事务超过500ms，禁止新的运动计划')
    return body


def integer(frame, key, bits=64, signed=True):
    return raw_integer(frame.get(key), bits, signed)


def scaled(value, scale=1, offset=0):
    return None if value is None else (value-offset)*scale


def pendulum_pose_degrees(frame):
    # Display the pre-clipping measurement; the controller's ADC12 angle is unchanged.
    quiet = integer(frame, 'quiet_q4', 14, False)
    zero = integer(frame, 'zero_q4', 14, False)
    flags = integer(frame, 'flags', 8, False) or 0
    if quiet is None or zero is None or not flags & 16:
        return None
    delta = (quiet-zero+8192) % 16384 - 8192
    return delta * POSE_Q4_DEG_SCALE


def gains(value):
    return list(value) if isinstance(value, (list, tuple)) and len(value) == 6 and all(finite(v) and v >= 0 for v in value) else None


def raw_copy(frame):
    result = safe_json(frame)
    for name in ('wide_encoder', 'wide_target', 'wide_physical_encoder', 'origin', 'final_target', 'tracking_error'):
        if name in frame:
            value = integer(frame, name)
            result[name] = str(value) if value is not None else None
    return result


class FD2TelemetryAdapter:
    def __init__(self):
        self.generation = 0
        self.clear()

    def clear(self):
        self.generation += 1
        self.source = None
        self.cursor = 0
        self.anchor = None
        self.last_t = None
        self.last_d = None
        self.last_e = None
        self.latest = None
        self.diagnostic = None
        self.extension = None
        self.pending_gap = True
        self.gesture_key = None
        self.gesture_received = 0

    def invalidate(self):
        # Keep cursor and identity so reconnecting the same bridge cannot replay old rows.
        self.latest = self.diagnostic = self.extension = None
        self.pending_gap = True

    def validate_batch(self, waveform, snapshot):
        if waveform is None:
            return []
        if not isinstance(waveform, dict) or not isinstance(waveform.get('stream'), str) or not waveform['stream']:
            raise ValueError('FD2 波形缺少 stream')
        sources = waveform.get('samples')
        if not isinstance(sources, list):
            raise ValueError('FD2 波形 samples 无效')
        previous = 0
        for source in sources:
            if not isinstance(source, dict) or type(source.get('cursor')) is not int or source['cursor'] <= previous:
                raise ValueError('FD2 样本游标必须严格递增')
            previous = source['cursor']
            if not finite(source.get('received_at')) or source['received_at'] <= 0:
                raise ValueError('FD2 样本接收时间无效')
            frame = source.get('frame')
            variant = snapshot['variant']
            profile = identify(snapshot)
            if profile in DELIVERY_PROFILES:
                _, uid, schema, wire = DELIVERY_PROFILES[profile]
            else:
                uid, schema, wire = IDENTITIES[variant]
            if not isinstance(frame, dict) or frame.get('kind') not in ('telemetry', 'diagnostic', 'compact', 'extension'):
                raise ValueError('FD2 样本类型无效')
            compact = frame.get('kind') == 'compact'
            if frame.get('kind') == 'extension' and profile not in ADB1_PROFILES:
                raise ValueError('当前固件不支持扩展帧')
            if compact and (profile not in V4_R28_PROFILES or not snapshot.get('synced') or
                            source.get('session') != snapshot.get('session')):
                raise ValueError('compact 样本没有绑定已认证的当前会话')
            if (frame.get('variant') != variant or frame.get('wire_version') != wire or
                    (not compact and (integer(frame, 'firmware_id', 32, False) != int(uid, 16) or
                     integer(frame, 'schema', 32, False) != int(schema, 16)))):
                raise ValueError('FD2 样本与已核验固件身份不一致')
            for key in ('board_ms', 'sequence') + (('control_epoch',) if frame['kind'] in ('telemetry', 'compact') else ()):
                if integer(frame, key, 32, False) is None:
                    raise ValueError('FD2 样本时刻或序号无效')
        return sources

    def convert(self, source, stream, profile):
        frame = source['frame']
        if source['cursor'] <= self.cursor:
            return None, False
        self.cursor = source['cursor']
        control = frame['kind'] in ('telemetry', 'compact')
        extension = frame['kind'] == 'extension'
        if control and frame.get('coherent') is not True:
            return None, False
        board = integer(frame, 'board_ms', 32, False)
        seq = integer(frame, 'sequence', 32, False)
        epoch = integer(frame, 'control_epoch', 32, False) if control else None
        last = self.last_t if control else self.last_e if extension else self.last_d
        if last is not None and (board, seq, epoch) == last:
            return None, False
        reset = False
        gap = self.pending_gap or source.get('gap_before', False)
        if control and last is not None:
            delta = (board-last[0]) & 0xffffffff
            if delta >= 0x80000000:
                self.generation += 1
                self.anchor = None
                self.diagnostic = None
                self.extension = None
                self.last_d = None
                self.last_e = None
                reset = gap = True
            elif delta != 5 or ((epoch-last[2]) & 0xffffffff) != (5 if profile in DELIVERY_PROFILES and profile != 'ADB1_07' else 1):
                gap = True
        if self.anchor is None:
            self.anchor = (board, source['received_at'])
        distance = (board-self.anchor[0]) & 0xffffffff
        if distance >= 0x80000000:
            distance -= 2**32
        at = self.anchor[1] + distance/1000
        row = dict(sample_id=f'{stream}:{self.generation}:{source["cursor"]}',
                   session_epoch=f'{stream}:{self.generation}', sequence=seq, board_ms=board,
                   fpga_time_ms=board, received_at=source['received_at'], plot_at=at,
                   measurement_at=at, gap_before=bool(gap), raw=raw_copy(frame))
        if control:
            actual_gains = gains(frame.get('active_gains'))
            base_gains = gains(frame.get('manual_gains'))
            row.update(channels=list(CONTROL_CHANNELS),
                       angle_deg=scaled(integer(frame, 'mean_adc12', 16, False), 345/4096, 3085),
                       pendulum_pose_deg=pendulum_pose_degrees(frame),
                       angle_display_limited=integer(frame, 'mean_adc12', 12, False) in (0, 4095),
                       alpha_ref_deg=scaled(integer(frame, 'target_angle_adc12', 16, False), 345/4096, 3085),
                       arm_angle_deg=scaled(integer(frame, 'wide_encoder'), COUNT_SCALE),
                       arm_reference_deg=scaled(integer(frame, 'wide_target'), COUNT_SCALE),
                       duty_actual_percent=scaled(integer(frame, 'actual_applied_q15', 16), 100/32768),
                       angle_kp=actual_gains[0] if actual_gains else None,
                       angle_kd=actual_gains[2] if actual_gains else None,
                       base_kp=base_gains[0] if base_gains else None,
                       base_kd=base_gains[2] if base_gains else None)
            if frame['kind'] == 'compact':
                # B has signed errors but no quiet Q4 or gain bank; never synthesize them from D.
                row['angle_deg'] = scaled(integer(frame, 'angle_error', 16), 345/4096)
                row['alpha_ref_deg'] = scaled(integer(frame, 'target_angle_error', 16), 345/4096)
                row['pendulum_pose_deg'] = None
                row['angle_display_limited'] = False
                if frame.get('range_valid') is not True:
                    row['arm_reference_deg'] = None
                else:
                    row['channels'].append('final_target_deg')
                    row['final_target_deg'] = scaled(integer(frame, 'final_target'), COUNT_SCALE)
            if frame.get('variant') == 16:
                row['channels'].append('final_target_deg')
                row['final_target_deg'] = scaled(integer(frame, 'final_target'), COUNT_SCALE)
            self.last_t = (board, seq, epoch)
            self.latest = row
            self.pending_gap = False
        elif extension:
            row.update(channels=[])
            self.last_e = (board, seq, epoch)
            self.extension = row
        else:
            final = integer(frame, 'final_target') if frame.get('final_target_valid') is True else None
            row.update(channels=['final_target_deg'], final_target_deg=scaled(final, COUNT_SCALE))
            self.last_d = (board, seq, epoch)
            self.diagnostic = row
        return row, reset

    def observe(self, snapshot, now, waveform, data):
        variant = snapshot['variant']
        profile = identify(snapshot)
        # Validate all input before advancing any cursor: a failed batch can be fetched again.
        sources = self.validate_batch(waveform, snapshot)
        stream = waveform['stream'] if waveform else snapshot.get('session')
        key = (profile, snapshot.get('session'), stream)
        reset = key != self.source
        if reset:
            self.clear()
            self.source = key
        overrun = bool(waveform and waveform.get('overrun'))
        if overrun:
            self.pending_gap = True
        rows = []
        for source in sources:
            row, changed = self.convert(source, stream, profile)
            if changed:
                rows.clear()
                reset = True
            if row is not None:
                rows.append(row)
        host = snapshot.get('host') or {}
        replay = snapshot.get('replay') or {}
        replaying = replay.get('active') is True
        simulated = replaying and replay.get('simulation') is True
        playback = replaying and not simulated
        live = bool(not replaying and snapshot.get('connected') and snapshot.get('synced') and fresh(snapshot.get('status_age_s')))
        simulated_live = bool(simulated and host.get('connected') and host.get('authenticated') and fresh(snapshot.get('status_age_s')))
        control_fresh = bool(self.latest and (playback or ((live or simulated_live) and fresh(snapshot.get('control_age_s')) and fresh(now-self.latest['received_at']))))
        diagnostic_fresh = bool(self.diagnostic and (playback or ((live or simulated_live) and fresh(snapshot.get('diagnostic_age_s')) and fresh(now-self.diagnostic['received_at']))))
        latest_state = host.get('latest') if isinstance(host.get('latest'), dict) else {}
        control = self.latest['raw'] if self.latest else {}
        current = control if replaying else latest_state
        state_available = live or (playback and bool(control)) or (simulated_live and bool(control))
        flags = integer(current, 'flags', 32, False) or 0
        mode = integer(current, 'mode', 8, False) if state_available else None
        fault = integer(current, 'fault', 8, False) if state_available else None
        pwm = integer(current, 'actual_applied_q15', 16) if state_available else None
        data.update(firmware=profile, source='fd2-host', host_online=True,
                    session=snapshot.get('session'), connected=bool(snapshot.get('connected')) and not replaying,
                    synced=bool(snapshot.get('synced')) and not replaying, status_fresh=live,
                    status_age_s=snapshot.get('status_age_s'), display_fresh=control_fresh,
                    pendulum_pose_deg=self.latest.get('pendulum_pose_deg') if control_fresh else None,
                    angle_display_limited=bool(control_fresh and self.latest.get('angle_display_limited')),
                    telemetry_fresh=control_fresh, replay=safe_json(replay), transport=safe_json(snapshot.get('transport') or {}),
                    capabilities=capabilities(profile), mode=mode, fault=fault,
                    mode_name={0:'IDLE', 1:'ZERO', 2:'ARMED', 3:'BALANCE', 4:'FAULT', 5:'SWING'}.get(mode, 'WAITING'),
                    requests=safe_json(snapshot.get('requests') or {}),
                    status=dict(mode=mode, fault=fault, pwm=pwm, pwm_zero=pwm == 0,
                                run_session=bool(flags & 2), swing_active=mode == 5, zero_valid=bool(flags & 16),
                                motor_permitted=bool(flags & 4), stopped_config_allowed=bool(live and mode in (0,1) and not flags & 6 and not fault)),
                    fd2=dict(control_fresh=control_fresh, diagnostic_fresh=diagnostic_fresh,
                             control_age_s=snapshot.get('control_age_s'), diagnostic_age_s=snapshot.get('diagnostic_age_s'),
                             crc_errors=host.get('crc_errors', 0), sequence_gaps=host.get('sequence_gaps', 0),
                             bridge_overrun=overrun, error=host.get('error', ''), log_error=host.get('log_error', ''),
                             log_status=host.get('log_status', ''), control_hz=200),
                    swing=dict(profile=current.get('swing_profile') if state_available else None,
                               stage=current.get('swing_stage') if state_available else None),
                    action_reason=current.get('action_reason') if state_available else None)
        if profile in ADB1_PROFILES:
            data['adb1_extension'] = (self.extension['raw'] if self.extension and
                (playback or ((live or simulated_live) and fresh(now-self.extension['received_at']))) else None)
        if profile == 'DS13':
            data['draw'] = dict(stroke=safe_json(host.get('stroke') or {}),
                                direction_readback=host.get('direction_readback'),
                                job_id=control.get('job_id') if control_fresh else None,
                                target=control.get('live_offset') if control_fresh else None,
                                point_sequence=control.get('point_sequence') if control_fresh else None,
                                play_state=control.get('play_state') if control_fresh else None)
        if profile == 'G3_C1':
            data['gowin'] = safe_json(host.get('gowin') or {})
        if profile in V4_R28_PROFILES | ADB1_PROFILES:
            independent = (host.get('independent') if profile in V4_R28_PROFILES else host.get('independent_gains')) if live else None
            if isinstance(independent, (list, tuple)) and len(independent) == 2:
                independent = independent[0]
            active = gains(control.get('active_gains')) if control_fresh else None
            if profile in V4_R28_PROFILES and diagnostic_fresh:
                diagnostic_raw = self.diagnostic['raw']
                active = gains(diagnostic_raw.get('active_gains')) if diagnostic_raw.get('coherent') is True else None
            data['pid_gains'] = dict(independent=gains(independent),
                                     stored=gains(host.get('stored') if profile in V4_R28_PROFILES else host.get('stored_gains')) if live else None,
                                     active=active)
        if control_fresh:
            data.update({name:self.latest.get(name) for name in CONTROL_CHANNELS})
            data.update(received_at=self.latest['received_at'], angle_received_at=self.latest['received_at'],
                        arm_received_at=self.latest['received_at'], fpga_time_ms=self.latest['board_ms'],
                        arm_fpga_time_ms=self.latest['board_ms'], encoder=control.get('wide_encoder'),
                        position_error=integer(control, 'position_error', 32), position_target=control.get('wide_target'))
        data['motion'] = dict(origin_count=None, final_target_count=None, final_target_deg=None,
                              actual_count=control.get('wide_encoder') if control_fresh else None,
                              reference_count=control.get('wide_target') if control_fresh else None,
                              request_id=None, action=None, planner_phase=None, trajectory_done=False,
                              tracking_settled=False, settle_ms=None, measured_velocity=None, tracking_error=None)
        if variant == 16 and control_fresh:
            data['motion'].update(final_target_count=control.get('final_target'),
                                  final_target_deg=self.latest.get('final_target_deg'),
                                  motion_state=control.get('motion_state'),
                                  tracking_error=control.get('tracking_error'))
        if diagnostic_fresh:
            diag = self.diagnostic['raw']
            final_valid = diag.get('final_target_valid') is True
            speed, error = integer(diag, 'measured_velocity', 32), integer(diag, 'tracking_error')
            data['motion'].update(measured_velocity=speed, measured_velocity_deg_s=scaled(speed, COUNT_SCALE),
                                  tracking_error=error, tracking_error_deg=scaled(error, COUNT_SCALE),
                                  stop_reason=diag.get('stop_reason'))
            if variant != 16:
                data['motion'].update(origin_count=diag.get('origin'), final_target_count=diag.get('final_target') if final_valid else None,
                                      final_target_deg=self.diagnostic['final_target_deg'], request_id=diag.get('motion_request_id'),
                                      action=diag.get('motion_action'), planner_phase=diag.get('planner_phase'),
                                      trajectory_done=diag.get('trajectory_done') is True, tracking_settled=diag.get('tracking_settled') is True,
                                      settle_ms=scaled(integer(diag, 'settle_ticks', 8, False), 5))
            data['arm_velocity_deg_s'] = scaled(speed, COUNT_SCALE)
        if profile == 'V4_USB_BLE':
            data['motion']['trajectory_done'] = None
            last_position = host.get('last_position_request') if live else None
            if not isinstance(last_position, dict) or type(last_position.get('value')) is not int or last_position['value'] not in range(1, 7):
                last_position = None
            data['motion']['last_position_request'] = safe_json(last_position)
            actual = integer(control, 'wide_encoder') if control_fresh and control.get('range_valid') is True else None
            target = integer(control, 'final_target') if control_fresh and control.get('range_valid') is True else None
            data['motion']['remaining_to_target_deg'] = scaled(target-actual, COUNT_SCALE) if actual is not None and target is not None else None
            reference = integer(control, 'wide_target') if control_fresh and control.get('range_valid') is True else None
            if actual is not None and reference is not None:
                data['motion']['tracking_error'] = safe_json(actual-reference)
                data['motion']['tracking_error_deg'] = scaled(actual-reference, COUNT_SCALE)
        if variant in (3, 16):
            data['adaptive'] = dict(manual_gains=gains(control.get('manual_gains')) if control_fresh else None,
                                    active_gains=gains(control.get('active_gains')) if control_fresh else None,
                                    target_gains=gains(control.get('adaptive_target_gains')) if control_fresh else None,
                                    enabled=bool(control_fresh and control.get('adaptive_enabled_at_launch')),
                                    used=bool(control_fresh and control.get('adaptive_used_at_launch')),
                                    freeze_reason=control.get('freeze_reason') if control_fresh else None,
                                    averages=safe_json(host.get('actual_gain_averages')) if control_fresh else None)
        if variant in (1, 2, 16) and state_available:
            seq = integer(current, 'gesture_event_seq', 32, False) or 0
            code = integer(current, 'gesture_code', 8, False) or 0
            gesture_key = (seq, code)
            changed = gesture_key != self.gesture_key
            new = changed and self.gesture_key is not None and seq > 0
            if changed:
                self.gesture_received = now
            self.gesture_key = gesture_key
            data['gesture_event'] = dict(code=code, sequence=seq, received_at=self.gesture_received,
                                         new_since_poll=new, board_ms=current.get('board_ms'))
        return safe_json(data), rows, reset
