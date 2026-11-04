"""Strict compact SwingTune framing; no serial or motion side effects."""
from dataclasses import dataclass
import struct

USER_CODE = 0x53413231
CONTROL_SCHEMA = 0x53415421
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


def decode(raw: bytes) -> Frame:
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

    def feed(self, data: bytes):
        self.buffer.extend(data)
        while len(self.buffer) >= 6:
            if self.buffer[:4] != b'J2A3' or int.from_bytes(self.buffer[4:6], 'little') != 44:
                del self.buffer[0]
                self.discarded_bytes += 1
                continue
            if len(self.buffer) < 44:
                break
            raw = bytes(self.buffer[:44])
            try:
                frame = decode(raw)
            except ValueError:
                del self.buffer[0]
                self.bad_frames += 1
                continue
            del self.buffer[:44]
            yield frame


def encode_ack(command_id, opcode, revision, readback=b'', status=0, reason=0):
    """Offline test encoder; never creates real-board evidence."""
    if len(readback) > 16:
        raise ValueError('readback too long')
    body = b'J2A3' + struct.pack('<HIIIBBBIB', 44, USER_CODE, CONTROL_SCHEMA,
        command_id, opcode, status, reason, revision, len(readback)) + readback.ljust(16, b'\0')
    return body + struct.pack('<H', crc16(body))
