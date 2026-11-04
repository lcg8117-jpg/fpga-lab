"""Paper E profile preparation; no inferred machine calibration or hardware IO."""
import json
import math
from pathlib import Path
from e68_protocol import scalar_packet

FIELDS = ('error_node_1', 'error_node_2', 'error_node_3',
          'delta_node_1', 'delta_node_2', 'delta_node_3',
          'kp_min', 'kp_max', 'kd_min', 'kd_max')


def number(value, name):
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise ValueError(name + ' must be a finite positive calibrated number')
    return float(value)


def compile_profile(profile, control=False):
    if profile.get('schema') != 'J280.E1993.profile.v1' or profile.get('sample_seconds') != .010:
        raise ValueError('Paper E profile at fixed10ms required')
    provenance = profile.get('provenance', {})
    kind = provenance.get('kind')
    sources = provenance.get('sources')
    if kind not in ('synthetic', 'model-screened', 'measured-screened') or not isinstance(sources, list) or not sources or any(not isinstance(x, str) or not x.strip() for x in sources):
        raise ValueError('Explicit profile provenance and sources required')
    if control and (kind == 'synthetic' or provenance.get('control_qualified') is not True):
        raise ValueError('Synthetic/unqualified profile cannot enable real E control')
    ku, tu = number(profile.get('Ku'), 'Ku'), number(profile.get('Tu_seconds'), 'Tu_seconds')
    nodes = []
    for name in ('error_nodes_rad', 'delta_nodes_rad_per_sample'):
        triple = profile.get(name)
        if not isinstance(triple, list) or len(triple) != 3:
            raise ValueError(name + ' requires three positive physical nodes')
        values = [number(x, name) for x in triple]
        quantized = [int(x * 2**20 + .5) for x in values]
        if not 0 < quantized[0] < quantized[1] < quantized[2] <= 0x7fffffff:
            raise ValueError(name + ' is unordered/unrepresentable in signed Q20')
        nodes.extend(quantized)
    gains = [.32 * ku, .60 * ku, .08 * ku * tu, .15 * ku * tu]
    qgains = [int(x * 2**24 + .5) for x in gains]
    if not all(0 < x <= 0xffffffff for x in qgains) or qgains[0] >= qgains[1] or qgains[2] >= qgains[3]:
        raise ValueError('Equation12 gains are unordered/unrepresentable in Q24')
    fields = nodes + qgains
    packets = []
    for i, value in enumerate(fields):
        packets.extend((scalar_packet(0x80 + i * 2, value & 65535),
                        scalar_packet(0x81 + i * 2, value >> 16)))
    packets.append(scalar_packet(0x73, 0xe001 if control else 0xe002))
    return dict(fields_u32=dict(zip(FIELDS, fields)), packets=packets,
                control_qualified=bool(control), provenance=provenance,
                scope='Numerical validation and provenance declaration only; no proof of plant stability')


def load_profile(path, control=False):
    return compile_profile(json.loads(Path(path).read_text(encoding='utf-8')), control)
