"""J2U1 wire codec. Layout authority: unified API-Contract, not legacy variants."""
import math
import struct

VERSION = 3
VARIANT = 0x10
FIRMWARE = 0x41444231
SCHEMA = 0x41445001
CAPABILITIES = 0x0f
DEFAULT_GAINS = (49., 0., 260., 25., 0., 620.)
STATUS, STOP, ZERO, RUN, CLEAR, ESTOP = 0x01, 0x10, 0x11, 0x12, 0x13, 0x14
SET_GAINS, READ_GAINS, ADAPT_ENABLE, ADAPT_DISABLE = 0x20, 0x21, 0x22, 0x23
SELECT_SWING, START_SWING, READ_CONFIG, WRITE_CONFIG = 0x30, 0x31, 0x32, 0x33
MOTION_CW, MOTION_CCW, MOTION_LEFT, MOTION_RIGHT, MOTION_STOP = range(0x40, 0x45)
READS = {STATUS, READ_GAINS, READ_CONFIG}
SAFE = READS | {STOP, ESTOP}
COMMANDS = SAFE | {ZERO, RUN, CLEAR, SET_GAINS, ADAPT_ENABLE, ADAPT_DISABLE,
                          SELECT_SWING, START_SWING, *range(0x40, 0x45), WRITE_CONFIG}
STATUS_TEXT = {0: "已接受", 1: "状态拒绝", 2: "格式错误", 3: "参数范围错误", 4: "未就绪", 5: "旧或重复请求", 6: "能力不支持"}
MODE_TEXT = {0: "安全待机", 1: "已记录零点 / 停止", 2: "等待平衡准入", 3: "平衡", 4: "故障", 5: "起摆"}
GESTURES = {0: "无", 1: "右滑", 2: "左滑", 3: "上滑", 4: "下滑STOP", 5: "靠近", 6: "远离", 7: "顺时针", 8: "逆时针", 9: "挥手（无动作）"}
FREEZE_TEXT = {0: "无冻结码（是否开启见enable）", 1: "停车禁止输出", 2: "故障禁止输出", 3: "非零积分：固定基准", 4: "基准超出资格", 5: "饱和冻结", 6: "幅度/跟踪误差冻结", 7: "噪声冻结", 8: "观察预热", 9: "中心试探", 10: "拒绝后冷却", 11: "参考轨迹过渡冻结", 12: "参考历史预热", 13: "响应太小，保留当前学习中心"}
LEARNING_TEXT = {0: "尚无事件", 1: "明确开启", 2: "明确关闭", 3: "手动接管", 4: "运行/故障/资格抑制", 5: "采样被守卫拒绝", 6: "P/D绝对值上探", 7: "P/D绝对值下探", 8: "保留试探中心", 9: "拒绝并回滚", 10: "冷却完成", 11: "返回原中心复核"}
ADOPTION_TEXT = {0: "尚无变化", 1: "自适应采用", 2: "人工基准采用", 3: "资格/守卫后回基准"}
MOTION_TEXT = {0: "保持", 1: "单圈轨迹", 2: "连续旋转", 3: "受控制动", 4: "保持目标 / 参考收尾"}
MOTION_REASON_TEXT = {0: "正常", 1: "未进入平衡", 2: "忙碌拒绝", 3: "同向重复", 4: "不支持", 5: "范围保护", 6: "已取消", 7: "保持当前位置", 8: "跟踪保护", 9: "轨迹结束待到位", 10: "已收敛", 11: "收敛超时仍保持目标", 12: "起点误差过大", 13: "反向手势减速", 14: "明确减速", 15: "平衡许可撤销", 16: "已接收，参考结束后接续；新请求可替换", 17: "指令间隔保护未到期"}
ACTION_REASON_TEXT = {0: "尚无动作事件", 1: "起摆请求已接受", 2: "起摆资格拒绝", 3: "运动请求已接受", 4: "运动请求被拒绝", 5: "当前状态不支持此手势", 6: "下滑STOP"}
GESTURE_DECODE_TEXT = {0: "正常", 1: "等待释放", 2: "手势仍保持", 3: "无效组合", 4: "指令间隔保护，屏蔽全部手势"}
AUTO_STATES = {0: "待机", 1: "等待自然下垂", 2: "回位", 3: "确认静止", 4: "起摆准备", 5: "起摆中", 6: "已接平衡", 7: "失败", 8: "取消"}


def crc16(data):
    value = 0xffff
    for byte in data:
        value ^= byte << 8
        for _ in range(8):
            value = ((value << 1) ^ 0x1021) & 0xffff if value & 0x8000 else (value << 1) & 0xffff
    return value


def gains_payload(values):
    if len(values) != 6:
        raise ValueError("需要六个P/I/D参数")
    numbers = tuple(float(value) for value in values)
    if any(not math.isfinite(value) or not 0 <= value <= 10000 for value in numbers):
        raise ValueError("PID必须是0..10000的有限数")
    return struct.pack("<6f", *(0. if value == 0 else value for value in numbers))


def request(opcode, request_id, group=0, payload=b""):
    if opcode not in COMMANDS or not 0 < request_id <= 0xffffffff:
        raise ValueError("未知操作或请求ID非法；禁止回绕重放")
    payload = bytes(payload)
    expected = 24 if opcode == SET_GAINS else 1 if opcode in (SELECT_SWING, READ_CONFIG) else 5 if opcode == WRITE_CONFIG else 0
    if len(payload) != expected:
        raise ValueError("请求长度错误")
    if opcode in (SET_GAINS, READ_GAINS):
        if group != 255:
            raise ValueError("PID组必须为255")
    elif opcode in (READ_CONFIG, WRITE_CONFIG):
        if group not in (0, 1, 2, 251, 253, 254):
            raise ValueError("配置组错误")
    elif group != 0:
        raise ValueError("操作组必须为0")
    frame = bytearray(48)
    frame[:4] = b"FD2C"
    frame[4:8] = bytes((VERSION, opcode, group, len(payload)))
    struct.pack_into("<I", frame, 8, request_id)
    frame[12:12 + len(payload)] = payload
    struct.pack_into("<H", frame, 46, crc16(frame[:46]))
    return bytes(frame)


def checked_crc(frame):
    return len(frame) >= 6 and struct.unpack_from("<H", frame, len(frame) - 2)[0] == crc16(frame[:-2])


def identity_matches(item):
    return (item["wire_version"] == VERSION and item["variant"] == VARIANT and
            item["firmware_id"] == FIRMWARE and
            (item["kind"] == "ack" or item["schema"] == SCHEMA))


def _fields(frame, spec):
    return {name: struct.unpack_from("<" + fmt, frame, offset)[0] for name, offset, fmt in spec}


def _float_bank(frame, offset):
    values = struct.unpack_from("<6f", frame, offset)
    if not all(math.isfinite(value) for value in values):
        raise ValueError("浮点证据含NaN/Inf，不能作为有效事务")
    return values


def decode(frame):
    if not checked_crc(frame):
        raise ValueError("CRC错误")
    if frame[:4] == b"FD2A" and len(frame) == 80:
        if frame[76:78] != b"\0\0":
            raise ValueError("ACK保留字节非零")
        result = _fields(frame, [("request_id", 8, "I"), ("stored_revision", 12, "I"),
            ("mutation_highwater", 16, "I"), ("control_epoch", 20, "I"),
            ("config_read_value", 48, "I"), ("config_revision", 52, "I"),
            ("flags", 60, "I"), ("active_revision", 64, "I"),
            ("adaptive_adoption_epoch", 68, "I"), ("firmware_id", 72, "I")])
        result.update(kind="ack", wire_version=frame[4], opcode=frame[5], status=frame[6],
                      variant=frame[7], stored_gains=_float_bank(frame, 24),
                      config_group=frame[56], config_id=frame[57], mode=frame[58], fault=frame[59])
        return result
    if len(frame) != 256 or frame[:4] not in (b"FD2T", b"FD2D", b"FD2E"):
        raise ValueError("不兼容的帧身份/长度")
    if frame[:4] == b"FD2T":
        result = _fields(frame, [(name, offset, "I") for name, offset in (
            ("sequence", 8), ("board_ms", 12), ("control_epoch", 16), ("active_revision", 20),
            ("stored_revision", 24), ("adaptive_adoption_epoch", 28), ("firmware_id", 32),
            ("schema", 36), ("position_component_epoch", 116), ("gesture_event_seq", 120),
            ("gesture_poll_seq", 124), ("telemetry_dropped", 224), ("rx_errors", 228),
            ("config_revision", 232), ("learning_revision", 236), ("candidate_feature_epoch", 240))])
        result.update(_fields(frame, [("fault", 40, "B"), ("swing_profile", 41, "B"), ("swing_stage", 42, "B"),
            ("motion_state", 43, "B"), ("gesture_code", 44, "B"), ("action_reason", 45, "B"),
            ("freeze_reason", 46, "B"), ("direction", 47, "b"), ("raw_adc", 48, "H"),
            ("zero_q4", 50, "H"), ("quiet_q4", 52, "H"), ("mean_adc12", 54, "H"),
            ("raw_guard_mean", 56, "H"), ("angle_error", 58, "h"), ("angle_diff", 60, "h"),
            ("actual_applied_q15", 62, "h"), ("physical_encoder_raw", 64, "i"),
            ("local_encoder", 68, "h"), ("local_target", 70, "h"), ("wide_encoder", 72, "q"),
            ("wide_target", 80, "q"), ("final_target", 88, "q"), ("position_error", 96, "i"),
            ("requested_moto", 100, "i"), ("angle_pwm", 104, "i"), ("position_pwm", 108, "i"),
            ("target_angle_adc12", 112, "H"), ("control_period_us", 114, "H"),
            ("gesture_errors", 244, "H"), ("raw43", 246, "B"), ("raw44", 247, "B"),
            ("center_p_offset", 248, "h"), ("center_d_offset", 250, "h"),
            ("fast_p_offset", 252, "b"), ("fast_d_offset", 253, "b")]))
        for name, offset in (("manual_gains", 128), ("active_gains", 152), ("adaptive_target_gains", 176), ("components", 200)):
            result[name] = _float_bank(frame, offset)
        result.update(kind="telemetry", wire_version=frame[4], variant=frame[5], flags=frame[6], mode=frame[7],
            coherent=bool(frame[6] & 1), adaptive_enabled_at_launch=bool(frame[6] & 32),
            adaptive_used_at_launch=bool(frame[6] & 64), motor_permitted_at_completion=bool(frame[6] & 4))
        result['actual_pwm_at_transaction_launch'] = result['actual_applied_q15']
        result['requested_moto_at_completion'] = result['requested_moto']
        result['tracking_error'] = result['position_error']
        result.update(control_epoch_stride=5, control_log_decimation=5 if result["control_period_us"]==1000 else 1, control_profile=int(result["control_period_us"]==1000))
        return result
    if frame[:4] == b"FD2E":
        result = _fields(frame, [("sequence", 8, "I"), ("board_ms", 12, "I"),
            ("schema", 16, "I"), ("firmware_id", 20, "I"), ("capabilities", 24, "I"),
            ("control_epoch", 28, "I"), ("control_ms", 32, "I"), ("context_flags", 36, "I"),
            ("physical_wide", 40, "q"), ("origin", 48, "q"), ("final_target", 56, "q"),
            ("wide_encoder", 64, "q"), ("wide_target", 72, "q"), ("position_error", 80, "q"),
            ("reference_velocity", 88, "i"), ("measured_velocity", 92, "i"),
            ("velocity_q", 96, "i"), ("acceleration_q", 100, "i"), ("motion_state", 104, "B"),
            ("direction", 105, "b"), ("planner_phase", 106, "B"), ("stop_reason", 107, "B"),
            ("settle_ticks", 108, "B"), ("motion_flags", 109, "B"), ("motion_action", 110, "B"),
            ("motion_reason", 111, "B"), ("motion_events", 112, "I"), ("motion_request_id", 116, "I"),
            ("gesture_event_seq", 120, "I"), ("gesture_poll_seq", 124, "I"),
            ("raw43", 128, "B"), ("raw44", 129, "B"), ("decode_reason", 130, "B"),
            ("decode_state", 131, "B"), ("gesture_ready", 132, "B"), ("gesture_busy", 133, "B"),
            ("gesture_error", 134, "B"), ("gesture_init_state", 135, "B"), ("gesture_errors", 136, "H"),
            ("gesture_id0", 138, "B"), ("gesture_id1", 139, "B"), ("gesture_suppressed", 140, "I"),
            ("feature_epoch", 144, "I"), ("candidate_feature_epoch", 148, "I"), ("candidate_revision", 152, "I"),
            ("adoption_count", 156, "I"), ("freeze_reason", 160, "B"), ("feature_freeze_reason", 161, "B"),
            ("adaptation_enabled", 162, "B"), ("adaptation_active", 163, "B"),
            ("active_revision", 164, "I"), ("stored_revision", 168, "I"), ("adopted_feature_epoch", 172, "I"),
            ("handoff_q4", 176, "H"), ("run_q4", 178, "H"), ("source_capture_flags", 180, "B"),
            ("source_reason", 181, "B"), ("capture_hold_ms", 182, "B"), ("swing_profile", 183, "B"),
            ("kit", 184, "B"), ("capabilities_low", 185, "B"), ("angle_period_ms", 186, "B"),
            ("position_period_ms", 187, "B"), ("mutation_highwater", 188, "I"), ("zero_q4", 192, "H"),
            ("target_angle_adc12", 194, "H"), ("auto_state", 200, "B"), ("auto_result", 201, "B"),
            ("auto_enabled", 202, "B"), ("auto_reference_valid", 203, "B"), ("auto_error", 204, "i"),
            ("auto_anchor", 208, "i"), ("auto_attempt", 212, "I"), ("auto_still_ms", 216, "H"),
            ("auto_bottom_q4", 218, "H")])
        if frame[196:200] != bytes(4) or struct.unpack_from('<I',frame,220)[0] != 0x41465031 or frame[252:254] != bytes(2):
            raise ValueError("E保留字节非零：布局不兼容")
        result.update(_fields(frame, [("fastp_tag",220,"I"),("control_period_us",224,"H"),
            ("angle_P_ms",226,"B"),("angle_D_ms",227,"B"),("position_P_ms",228,"B"),("position_D_ms",229,"B"),
            ("control_profile",230,"B"),("control_log_decimation",231,"B"),
            ("angle_D_epoch_ms",232,"I"),("position_D_epoch_ms",236,"I"),("position_P_epoch_ms",240,"I"),
            ("policy_epoch_5ms",244,"I"),("latest_control_epoch_ms",248,"I")]))
        result.update(kind="extension", wire_version=frame[4], variant=frame[5], flags=frame[6], mode=frame[7])
        for name, bit in (("motion_session", 0), ("position_update", 1), ("feature_frozen", 2),
                          ("velocity_valid", 3), ("context_enabled", 4), ("context_active", 5), ("context_used", 6)):
            result[name] = bool(result['context_flags'] & (1 << bit))
        result['trajectory_done'] = bool(result['motion_flags'] & 2)
        result['tracking_settled'] = bool(result['motion_flags'] & 4)
        return result
    if frame[6] != 1:
        raise ValueError("诊断布局错误")
    result = _fields(frame, [("sequence", 8, "I"), ("board_ms", 12, "I"), ("schema", 16, "I"),
        ("firmware_id", 20, "I"), ("last_completed_epoch", 24, "I"), ("last_completed_ms", 28, "I"),
        ("kit", 32, "B"), ("zero_valid", 33, "B"), ("swing_failure_reason", 34, "B"),
        ("swing_flags", 35, "B"), ("handoff_wait_ms", 36, "H"), ("delta_q4", 38, "h"),
        ("physical_encoder_raw", 40, "i"), ("invalid_transitions", 44, "H"), ("minimum_edge_clocks", 46, "H"),
        ("physical_counts_per_rev", 48, "I"), ("forest_counts_per_rev", 52, "I"),
        ("measured_velocity", 56, "i"), ("reference_velocity", 60, "i"), ("tracking_error", 64, "q"),
        ("settle_ticks", 72, "B"), ("stop_reason", 73, "B"), ("tracking_flags", 74, "B"),
        ("source_reason", 75, "B"), ("gesture_map", 76, "B"), ("left_positive", 77, "B"),
        ("cw_positive", 78, "B"), ("capture_hold_ms", 79, "B"), ("adoption_event_seq", 80, "I"),
        ("adoption_event_ms", 84, "I"), ("adoption_control_epoch", 88, "I"), ("adoption_feature_epoch", 92, "I"),
        ("adoption_reason", 96, "B"), ("last_adaptive_used", 97, "B"), ("last_freeze", 98, "B"),
        ("learning_code", 99, "B"), ("learning_event_seq", 100, "I"), ("learning_feature_epoch", 104, "I"),
        ("learning_event_ms", 108, "I"), ("learning_reason", 112, "B"), ("center_before", 113, "b"),
        ("center_after", 114, "b"), ("learning_phase", 115, "B"), ("window_samples", 116, "B"),
        ("cooldown_windows", 117, "B"), ("reference_peak", 118, "H"), ("window_peak", 120, "H"),
        ("reference_energy", 124, "I"), ("window_energy", 128, "I"), ("candidate_feature_epoch", 180, "I"),
        ("candidate_revision", 184, "I"), ("adopted_feature_epoch", 188, "I"),
        ("stored_revision", 192, "I"), ("active_revision", 196, "I"), ("adoption_count", 200, "I"),
        ("swing_finished_ms", 204, "I"), ("handoff_ms", 208, "I"), ("swing_failure_ms", 212, "I"),
        ("source_alpha", 216, "i"), ("source_alpha_rate", 220, "i"), ("source_arm_rate", 224, "i"),
        ("source_capture_flags", 228, "B"), ("event_samples", 229, "B")])
    for name, offset in (("adoption_before_gains", 132), ("adoption_after_gains", 156)):
        result[name] = _float_bank(frame, offset)
    result.update(kind="diagnostic", wire_version=frame[4], variant=frame[5], flags=frame[7],
                  trajectory_done=bool(result['tracking_flags'] & 1), tracking_settled=bool(result['tracking_flags'] & 2))
    if frame[230] == 0xA7 and frame[251] == 1:
        result.update(_fields(frame, [("auto_state", 231, "B"), ("auto_attempt", 232, "I"),
            ("auto_error", 236, "i"), ("auto_still_ms", 240, "H"), ("auto_pairs", 242, "B"),
            ("auto_result", 243, "B"), ("auto_anchor", 244, "i"), ("auto_bottom_q4", 248, "H"),
            ("auto_flags", 250, "B")]))
    return result


class FrameParser:
    def __init__(self):
        self.buffer = bytearray()
        self.crc_errors = self.format_errors = self.discarded_bytes = 0

    def feed(self, data):
        self.buffer.extend(data)
        result = []
        while len(self.buffer) >= 4:
            positions = [value for magic in (b"FD2A", b"FD2T", b"FD2D", b"FD2E") if (value := self.buffer.find(magic)) >= 0]
            if not positions:
                count = max(0, len(self.buffer) - 3)
                del self.buffer[:count]
                self.discarded_bytes += count
                break
            count = min(positions)
            if count:
                del self.buffer[:count]
                self.discarded_bytes += count
            length = 80 if self.buffer[:4] == b"FD2A" else 256
            if len(self.buffer) < length:
                break
            raw = bytes(self.buffer[:length])
            try:
                item = decode(raw)
            except (ValueError, struct.error):
                if not checked_crc(raw):
                    self.crc_errors += 1
                else:
                    self.format_errors += 1
                del self.buffer[0]
                self.discarded_bytes += 1
                continue
            del self.buffer[:length]
            result.append((raw, item))
        return result
