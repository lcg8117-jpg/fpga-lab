"""Strict compact SwingTune framing; no serial or motion side effects."""
from dataclasses import dataclass
import struct

USER_CODE = 0x53503234
CONTROL_SCHEMA = 0x53505424
COMMAND_HEADER = struct.Struct('<4sHIIIB')


def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ (0x1021 if crc & 0x8000 else 0)) & 0xFFFF
    return crc


def signed(value: int, bits: int) -> int:
    value &= (1 << bits) - 1
    return value - (1 << bits) if value & (1 << (bits - 1)) else value


def request(command_id: int, opcode: int, payload: bytes = b'') -> bytes:
    if type(command_id) is not int or not 0 <= command_id <= 0xFFFFFFFF:
        raise ValueError('command_id out of range')
    if opcode not in (0, 1, 2, 3, 4, 5, 0x70, 0x71, 0x72) or len(payload) > 16:
        raise ValueError('unsupported command or payload')
    body = COMMAND_HEADER.pack(b'J2C3', 21 + len(payload), USER_CODE,
                               CONTROL_SCHEMA, command_id, opcode) + payload
    return body + struct.pack('<H', crc16(body))


@dataclass(frozen=True)
class Frame:
    raw: bytes
    kind: str
    values: dict


def wide_integer_strings(values):
    """Portable JSON companions for consumers whose numbers have53-bit mantissas."""
    return {name: str(values[name]) for name in
            ('encoder_count', 'target_count_q32', 'reference_rad_q20') if name in values}


def decode(raw: bytes) -> Frame:
    if raw[:4] == b'J2T4':
        if len(raw) != 80 or int.from_bytes(raw[4:6], 'little') != 80:
            raise ValueError('wrong telemetry frame length')
        if crc16(raw[:-2]) != int.from_bytes(raw[-2:], 'little'):
            raise ValueError('CRC mismatch')
        if struct.unpack_from('<II', raw, 6) != (USER_CODE, CONTROL_SCHEMA):
            raise ValueError('firmware identity/schema mismatch')
        data = raw[14:78]
        sequence, stamp, capture, alpha, alpha_rate, theta_rate = struct.unpack_from('<IIIiii', data)
        encoder = int.from_bytes(data[24:32], 'little', signed=True)
        target = int.from_bytes(data[32:44], 'little', signed=True)
        reference = int.from_bytes(data[44:52], 'little', signed=True)
        pwm, flags, fault, angle_revision, position_revision = struct.unpack_from('<hBBII', data, 52)
        if flags & 7 > 4:
            raise ValueError('invalid telemetry mode')
        return Frame(raw, 'telemetry', dict(sequence=sequence, timestamp_ms=stamp,
            capture_ms=capture, alpha_q20=alpha, alpha_dot_q20=alpha_rate,
            theta_dot_q20=theta_rate, encoder_count=encoder, target_count_q32=target,
            reference_rad_q20=reference, pwm=pwm, flags=flags, fault=fault,
            mode=flags & 7, pid_valid=bool(flags & 8), velocity_valid=bool(flags & 16),
            motor=bool(flags & 32), zero_valid=bool(flags & 64), saturation=bool(flags & 128),
            angle_revision=angle_revision, position_revision=position_revision))
    if len(raw) != 44 or raw[:4] != b'J2A3' or int.from_bytes(raw[4:6], 'little') != 44:
        raise ValueError('wrong ACK frame length/magic')
    if crc16(raw[:-2]) != int.from_bytes(raw[-2:], 'little'):
        raise ValueError('CRC mismatch')
    if struct.unpack_from('<II', raw, 6) != (USER_CODE, CONTROL_SCHEMA):
        raise ValueError('firmware identity/schema mismatch')
    if raw[19] > 1 or raw[20] > 7 or (raw[19] == 0 and raw[20] != 0) or raw[25] > 16:
        raise ValueError('invalid ACK fields')
    return Frame(raw, 'ack', {
        'command_id': int.from_bytes(raw[14:18], 'little'), 'opcode': raw[18],
        'status': raw[19], 'reason': raw[20],
        'revision': int.from_bytes(raw[21:25], 'little'),
        'readback_hex': raw[26:26 + raw[25]].hex()})


class StreamDecoder:
    def __init__(self):
        self.buffer = bytearray()
        self.bad_frames = 0
        self.discarded_bytes = 0
        self.crc_errors = 0
        self.identity_errors = 0

    def feed(self, data: bytes):
        self.buffer.extend(data)
        while len(self.buffer) >= 6:
            expected = {b'J2A3': 44, b'J2T4': 80}.get(bytes(self.buffer[:4]))
            if expected is None or int.from_bytes(self.buffer[4:6], 'little') != expected:
                del self.buffer[0]
                self.discarded_bytes += 1
                continue
            if len(self.buffer) < expected:
                break
            raw = bytes(self.buffer[:expected])
            try:
                frame = decode(raw)
            except ValueError as exc:
                del self.buffer[0]
                self.bad_frames += 1
                self.crc_errors += 'CRC' in str(exc)
                self.identity_errors += 'identity' in str(exc)
                continue
            del self.buffer[:expected]
            yield frame


def encode_ack(command_id, opcode, revision, readback=b'', status=0, reason=0):
    """Offline test encoder; never creates real-board evidence."""
    if len(readback) > 16:
        raise ValueError('readback too long')
    body = b'J2A3' + struct.pack('<HIIIBBBIB', 44, USER_CODE, CONTROL_SCHEMA,
        command_id, opcode, status, reason, revision, len(readback)) + readback.ljust(16, b'\0')
    return body + struct.pack('<H', crc16(body))
