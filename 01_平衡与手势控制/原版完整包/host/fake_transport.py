"""Protocol-only fake device for preview/tests. It is not a plant/controller model."""
import math
import struct
import threading
import time

from . import protocol as p
from .config_fields import fields_for


def finish(frame):
    struct.pack_into("<H", frame, len(frame) - 2, p.crc16(frame[:-2]))
    return bytes(frame)


class FakeTransport:
    simulated = True

    def __init__(self, *, corrupt_epochs=(), drop_epochs=(), identity=p.FIRMWARE,
                 initial_highwater=0, suppress_opcodes=()):
        self.owner_thread = threading.get_ident()
        self.called_threads = set()
        self.started = time.monotonic()
        self.closed = False
        self.freeze_rx = False
        self.buffer = bytearray()
        self.epoch = 0
        self.sequence = 0
        self.diag_sequence = 0
        self.mode = 1
        self.fault = 0
        self.highwater = initial_highwater
        self.identity = identity
        self.config_revision = 0
        self.gain_revision = 0
        self.gains = p.DEFAULT_GAINS
        self.active_gains = self.gains
        self.adopted = 0
        self.active_used = False
        self.adopted_feature = 0
        self.enabled = False
        self.selected = 0
        self.motion = 0
        self.reference = 0
        self.actual = 0
        self.final = 0
        self.velocity = 0
        self.freeze = 1
        self.writes = []
        self.write_times = []
        self.suppress_opcodes = set(suppress_opcodes)
        self.corrupt_epochs = set(corrupt_epochs)
        self.drop_epochs = set(drop_epochs)
        self.config = {(group, field_id): minimum for group in (0, 1, 2, 251, 253, 254)
                       for field_id, _, minimum, _ in fields_for(group)}
        for group in (0, 1):
            self.config.update({(group, key): value for key, value in
                                ((0, 70), (1, 60 if group == 0 else 70),
                                 (2, 240 if group == 0 else 230), (3, 60), (4, 3000),
                                 (5, 230), (6, 1800), (8, 1), (9, 10))})
        self.config.update({(2, 0): 70, (2, 64): 90, (2, 65): 90, (2, 66): 90,
                            (254, 0): 0, (254, 1): 0, (254, 2): 1, (254, 3): 1,
                            (254, 4): 1, (254, 5): 0, (254, 6): 1080, (254, 7): 1040,
                            (254, 8): p.CAPABILITIES, (254, 9): p.SCHEMA, (254, 10): p.VERSION,
                            (253, 0): 0x41525431, (253, 1): 0, (253, 14): 20,
                            (253, 15): 8000, (253, 16): 500})

    def _thread(self):
        self.called_threads.add(threading.get_ident())
        if threading.get_ident() != self.owner_thread:
            raise RuntimeError("Fake transport detected more than one serial owner")

    def write(self, raw):
        self._thread()
        if self.closed:
            raise OSError("Fake transport closed")
        if len(raw) != 48 or raw[:4] != b"FD2C" or not p.checked_crc(raw):
            raise ValueError("Invalid fake request")
        opcode, group, length = raw[5:8]
        request_id = struct.unpack_from("<I", raw, 8)[0]
        payload = raw[12:12 + length]
        self.writes.append(bytes(raw))
        self.write_times.append((opcode, time.monotonic()))
        status = 0
        if opcode not in p.READS:
            if request_id <= self.highwater:
                status = 5
            self.highwater = max(self.highwater, request_id)
        if status == 0:
            if opcode in (p.STOP, p.ESTOP):
                self.mode = 1
                self.enabled = False
                self.motion = self.velocity = 0
                if opcode == p.ESTOP:
                    self.fault = 1
            elif opcode == p.RUN:
                self.mode = 3
            elif opcode == p.START_SWING:
                self.mode = 5
            elif opcode == p.CLEAR:
                self.fault = 0
            elif opcode == p.SELECT_SWING:
                if payload[0] < 3:
                    self.selected = payload[0]
                    self.config[(254, 0)] = self.selected
                else:
                    status = 3
            elif opcode == p.SET_GAINS:
                self.gains = struct.unpack("<6f", payload)
                self.gain_revision += 1
                self.enabled = False
            elif opcode in (p.ADAPT_ENABLE, p.ADAPT_DISABLE):
                self.enabled = opcode == p.ADAPT_ENABLE
            elif opcode == p.WRITE_CONFIG:
                key = (group, payload[0])
                self.config[key] = struct.unpack_from("<I", payload, 1)[0]
                self.config_revision += 1
            elif p.MOTION_CW <= opcode <= p.MOTION_STOP:
                if self.mode != 3:
                    status = 1
                elif opcode == p.MOTION_STOP:
                    self.motion = 3
                else:
                    sign = 1 if opcode in (p.MOTION_CW, p.MOTION_LEFT) else -1
                    if self.motion == 2 and self.velocity * sign < 0:
                        self.motion = 3
                    else:
                        self.motion = 1 if opcode in (p.MOTION_CW, p.MOTION_CCW) else 2
                        self.velocity = 3 * sign
                        self.final = self.reference + sign * 1040
        if opcode not in self.suppress_opcodes:
            ack = bytearray(80)
            ack[:4] = b"FD2A"
            ack[4:8] = bytes((p.VERSION, opcode, status, p.VARIANT))
            struct.pack_into("<4I", ack, 8, request_id, self.gain_revision, self.highwater, self.epoch)
            struct.pack_into("<6f", ack, 24, *self.gains)
            field_id = payload[0] if payload else 0
            value = self.config.get((group, field_id), 0xffffffff)
            struct.pack_into("<II", ack, 48, value, self.config_revision)
            ack[56:60] = bytes((group, field_id, self.mode, self.fault))
            struct.pack_into("<4I", ack, 60, 1 | (int(self.enabled) << 5), self.gain_revision, self.adopted, self.identity)
            self.buffer.extend(finish(ack))
        return len(raw)

    def _step(self):
        self.epoch += 1
        self.sequence += 1
        if self.motion in (1, 2, 3):
            if self.motion == 3:
                self.velocity = max(0, self.velocity - 1) if self.velocity > 0 else min(0, self.velocity + 1)
            self.reference += self.velocity
            if self.motion == 1 and (self.final - self.reference) * self.velocity <= 0:
                self.reference = self.final
                self.velocity = 0
                self.motion = 4
            if self.motion == 3 and not self.velocity:
                self.final = self.reference
                self.motion = 4
        self.actual = self.reference - int(math.sin(self.epoch / 12) * 3) if self.mode == 3 else self.reference
        self.freeze = 0 if not self.enabled else 1 if self.mode != 3 else 11 if self.motion == 3 else 8 if self.epoch < 30 else 0
        used = bool(self.enabled and self.mode == 3 and self.freeze == 0)
        if self.epoch % 5 == 0:
            previous = self.active_gains
            self.active_gains = tuple(value * (1 + (2 / 256 if index == 2 and used else 0)) for index, value in enumerate(self.gains))
            self.active_used = used
            self.adopted_feature = self.epoch - 1 if used else 0
            if previous != self.active_gains:
                self.adopted += 1
        frame = bytearray(256)
        frame[:4] = b"FD2T"
        frame[4:8] = bytes((p.VERSION, p.VARIANT, 1 | (4 if self.mode == 3 else 0) | 16 | (32 if self.enabled else 0) | (64 if self.active_used else 0), self.mode))
        struct.pack_into("<8I", frame, 8, self.sequence, self.epoch * 5, self.epoch * 5,
                         self.gain_revision, self.gain_revision, self.adopted, self.identity, p.SCHEMA)
        frame[40:48] = bytes((self.fault, self.selected, 1 if self.mode == 5 else 0, self.motion, 0, 0, self.freeze, 1))
        angle = int(math.sin(self.epoch / 16) * 8) if self.mode == 3 else 0
        struct.pack_into("<5H3h", frame, 48, 770, 12340, 12340, 3085 + angle, 3085,
                         angle, 0, 0)
        struct.pack_into("<i2h3qi", frame, 64, int(self.actual / 1040 * 1080),
                         10000 + self.actual - self.reference, 10000,
                         self.actual, self.reference, self.final, self.actual - self.reference)
        struct.pack_into("<HH3I", frame, 112, 3085, 1000, self.epoch * 5, 0, self.epoch)
        for offset, values in ((128, self.gains), (152, self.active_gains), (176, self.active_gains), (200, (0.,) * 6)):
            struct.pack_into("<6f", frame, offset, *values)
        struct.pack_into("<3I", frame, 232, self.config_revision, self.gain_revision, max(0, self.epoch - 1))
        raw = finish(frame)
        if self.epoch in self.corrupt_epochs:
            raw = raw[:50] + bytes((raw[50] ^ 1,)) + raw[51:]
        if self.epoch not in self.drop_epochs:
            self.buffer.extend(raw)
        if self.epoch % 10 == 0:
            self.buffer.extend(self._diagnostic())
            self.buffer.extend(self._extension())

    def _diagnostic(self):
        frame = bytearray(256)
        frame[:4] = b"FD2D"
        frame[4:8] = bytes((p.VERSION, p.VARIANT, 1, 0))
        self.diag_sequence += 1
        struct.pack_into("<6I", frame, 8, self.diag_sequence, self.epoch * 5, p.SCHEMA,
                         self.identity, self.epoch, self.epoch * 5)
        frame[33] = 1
        struct.pack_into("<IIiiq", frame, 48, 1080, 1040, self.velocity * 200, self.velocity * 200,
                         self.actual - self.reference)
        frame[74] = 3 if self.motion in (0, 4) else 0
        struct.pack_into("<4I", frame, 80, self.adopted, self.epoch * 5, self.epoch - self.epoch % 5,
                         max(0, self.epoch - self.epoch % 5 - 1))
        frame[96:100] = bytes((1 if self.enabled else 2, int(self.enabled), self.freeze, 1 if self.enabled else 2))
        struct.pack_into("<6f", frame, 132, *self.gains)
        struct.pack_into("<6f", frame, 156, *self.active_gains)
        struct.pack_into("<6I", frame, 180, max(0, self.epoch - 1), self.gain_revision,
                         max(0, self.epoch - 1), self.gain_revision, self.gain_revision, self.adopted)
        frame[230] = 0xA7
        frame[251] = 1
        return finish(frame)

    def _extension(self):
        frame = bytearray(256)
        frame[:4] = b"FD2E"
        frame[4:8] = bytes((p.VERSION, p.VARIANT, 0, self.mode))
        context = int(self.motion != 0) | 8 | (16 if self.enabled else 0) | (32 if self.enabled and self.freeze == 0 else 0) | (64 if self.active_used else 0)
        struct.pack_into("<8I", frame, 8, self.diag_sequence, self.epoch * 5, p.SCHEMA,
                         self.identity, p.CAPABILITIES, self.epoch * 5, self.epoch * 5, context)
        struct.pack_into("<6q4i", frame, 40, int(self.actual / 1040 * 1080), 0, self.final,
                         self.actual, self.reference, self.actual - self.reference,
                         self.velocity * 200, self.velocity * 200, self.velocity * 65536, 0)
        frame[104] = self.motion
        struct.pack_into("<b", frame, 105, (1 if self.velocity > 0 else -1) if self.velocity else 0)
        frame[109] = 6 if self.motion in (0, 4) else 1
        struct.pack_into("<4I", frame, 112, 0, self.highwater, 0, self.epoch)
        frame[132] = 1
        struct.pack_into("<4I", frame, 144, max(0, self.epoch - 1), max(0, self.epoch - 1), self.gain_revision, self.adopted)
        frame[160:164] = bytes((self.freeze, 11 if self.motion == 3 else 0,
                                int(self.enabled), int(self.enabled and self.freeze == 0)))
        struct.pack_into("<3I2H", frame, 164, self.gain_revision, self.gain_revision,
                         self.adopted_feature, 634, 272)
        frame[183:188] = bytes((self.selected, 0, p.CAPABILITIES, 1, 5))
        struct.pack_into("<I2H", frame, 188, self.highwater, 12340, 3085)
        struct.pack_into("<IH6B5I", frame, 220, 0x41465031, 1000, 1, 5, 5, 25, 1, 5,
                         self.epoch*5, self.epoch*5-self.epoch*5%25, self.epoch*5, self.epoch, self.epoch*5)
        return finish(frame)

    def read(self, maximum):
        self._thread()
        if self.freeze_rx:
            time.sleep(.002)
            return b""
        due = int((time.monotonic() - self.started) / .005)
        while self.epoch < due:
            self._step()
        if not self.buffer:
            time.sleep(.001)
        raw = bytes(self.buffer[:maximum])
        del self.buffer[:maximum]
        return raw

    def close(self):
        self._thread()
        self.closed = True
