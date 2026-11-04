"""Presentation-only G/GOWIN paths driven by observed waveform samples.

These layouts are not physical encoder coordinates or motor commands. The
existing measured trace, raw logging, input plans and control scores stay separate.
"""
import bisect
import math

SAMPLE_MS = 5
COUNTS_PER_TURN = 1040
MAX_WINDOW_MS = 150000


def _path_length(points):
    return sum(math.hypot(b[0]-a[0], b[1]-a[1]) for a, b in zip(points, points[1:]))


def _sample_path(points, count):
    distances = [0.0]
    for a, b in zip(points, points[1:]):
        distances.append(distances[-1]+math.hypot(b[0]-a[0], b[1]-a[1]))
    result = []
    for k in range(count):
        position = distances[-1]*k/(count-1)
        i = min(len(points)-2, max(0, bisect.bisect_right(distances, position)-1))
        fraction = (position-distances[i])/(distances[i+1]-distances[i])
        result.append(tuple(a+(b-a)*fraction for a, b in zip(points[i], points[i+1])))
    return result


def _letter_paths():
    # The raised, extended crossbar distinguishes the display glyph from C.
    arc = [(50*math.cos(math.radians(65+250*k/250)),
            50*math.sin(math.radians(65+250*k/250))) for k in range(251)]
    g = arc+[(arc[-1][0], -10.0), (-12.0, -10.0)]
    o = [(50*math.cos(math.radians(45+360*k/256)),
          50*math.sin(math.radians(45+360*k/256))) for k in range(257)]
    return {'G': g, 'O': o,
            'W': [(-95, 65), (-48, -65), (0, 35), (48, -65), (95, 65)],
            'I': [(-40, 55), (40, 55), (0, 55), (0, -55), (-40, -55), (40, -55)],
            'N': [(-50, -55), (-50, 55), (50, -55), (50, 55)]}


def word_layout(word, *, start_ms=42000, end_ms=72000, window_ms=36000,
                half_width=28):
    """Build a known display layout without reading any measured samples."""
    if word not in ('G', 'GOWIN'):
        raise ValueError('Only G and GOWIN visualization layouts are supported')
    if any(type(v) is not int for v in (start_ms, end_ms, window_ms)):
        raise ValueError('Expected integer display times')
    if not (0 <= start_ms < end_ms and 0 < window_ms <= MAX_WINDOW_MS):
        raise ValueError('Invalid display interval')
    if (end_ms-start_ms) % SAMPLE_MS or not 10*len(word) <= end_ms-start_ms <= MAX_WINDOW_MS:
        raise ValueError('Display interval must match the 5 ms acquisition cadence')
    if isinstance(half_width, bool) or not math.isfinite(half_width) or not 2 <= half_width <= 28:
        raise ValueError('Display half-width must be between 2 and 28')
    paths = _letter_paths()
    total = (end_ms-start_ms)//SAMPLE_MS
    scale = 2*math.pi/(COUNTS_PER_TURN*(window_ms/1000))
    letters = []
    cursor = 0.0
    for letter in word:
        path = paths[letter]
        low, high = min(p[0] for p in path), max(p[0] for p in path)
        offset = cursor+half_width-low
        centres = _sample_path(path, total)
        letters.append({'letter': letter, 'start_ms': start_ms,
                        'end_ms': end_ms, 'offset_x': offset,
                        'reference': [(x, y*scale) for x, y in centres],
                        'passes': [{'start_index': 0, 'end_index': total,
                                    'start_ms': start_ms, 'end_ms': end_ms}]})
        cursor += high-low+2*half_width+28
    width = cursor-28
    for item in letters:
        item['offset_x'] -= width/2
    return {'word': word, 'start_ms': start_ms, 'end_ms': end_ms,
            'window_ms': window_ms, 'half_width': float(half_width),
            'full_width': float(half_width)*2, 'width': width,
            'y_limit': 65+half_width, 'y_scale': scale, 'letters': letters,
            'presentation_only': True, 'reference_used': True,
            'control_validation': False, 'positions_changed': True}


def visualize_word(actual_rows, word, *, origin, start_ms=42000, end_ms=72000,
                   window_ms=36000, half_width=28):
    """Display one observed signal in independent G/GOWIN typography views.

    Each view uses each accepted source point once, with the original time and
    source index. Multiple views deliberately reuse the same signal; they are
    not independent acquisitions. A common causal 1 s EMA removes the slowly
    moving input center before bounded local fluctuations are placed on each
    glyph. No oscillator, random signal, interpolation or extra sample is used.
    """
    layout = word_layout(word, start_ms=start_ms, end_ms=end_ms,
                         window_ms=window_ms, half_width=half_width)
    if isinstance(origin, bool) or not isinstance(origin, (int, float)) or not math.isfinite(origin):
        raise ValueError('Origin must be finite')
    local = []
    previous_seen = previous_valid = mean = None
    valid = invalid = outside = input_count = 0
    limit = .85*half_width
    for source_index, row in enumerate(actual_rows):
        input_count += 1
        if row is None:
            local.append(None)
            previous_valid = mean = None
            continue
        try:
            ms, x, y = row
            if any(isinstance(v, bool) or not isinstance(v, (int, float)) or
                   not math.isfinite(v) for v in (ms, x, y)) or int(ms) != ms:
                raise ValueError('Invalid source sample')
            ms = int(ms)
            if previous_seen is not None and ms <= previous_seen:
                raise ValueError('Source timestamps must advance')
            previous_seen = ms
            if not start_ms <= ms < end_ms:
                outside += 1
                continue
            if (ms-start_ms) % SAMPLE_MS:
                raise ValueError('Source sample is not aligned to the 5 ms grid')
            raw = (x-origin, y/layout['y_scale'])
            if not all(math.isfinite(v) for v in raw):
                raise ArithmeticError('Source exceeds arithmetic range')
            if previous_valid is not None and ms-previous_valid != SAMPLE_MS:
                local.append(None)
                mean = None
            if mean is None:
                mean = raw
            alpha = -math.expm1(-SAMPLE_MS/1000)
            mean = tuple(m+alpha*(v-m) for m, v in zip(mean, raw))
            dx, dy = (v-m for v, m in zip(raw, mean))
            radius = math.hypot(dx, dy)
            factor = limit*math.tanh(radius/limit)/radius if radius else 0.
            if not all(math.isfinite(v) for v in (radius, factor)):
                raise ArithmeticError('Fluctuation exceeds arithmetic range')
            local.append((source_index, ms, dx*factor, dy*factor))
            previous_valid = ms
            valid += 1
        except (ValueError, TypeError, OverflowError, ArithmeticError):
            local.append(None)
            previous_valid = mean = None
            invalid += 1
    # Display connectors may reference these existing fluctuations separately.
    layout['observed_fluctuations'] = local
    result = []
    for item in layout['letters']:
        mapped, indices = [], []
        for point in local:
            if point is None:
                mapped.append(None)
                indices.append(None)
                continue
            source_index, ms, dx, dy = point
            index = (ms-start_ms)//SAMPLE_MS
            cx, cy = item['reference'][index]
            mapped.append((ms, origin+item['offset_x']+cx+dx,
                           cy+dy*layout['y_scale']))
            indices.append(source_index)
        result.append({'letter': item['letter'], 'rows': mapped, 'source_indices': indices})
    metadata = {'format': 'J280WordVisualizationV1', 'word': word,
                'presentation_only': True, 'actual_measurement': False,
                'control_validation': False, 'reference_used': True,
                'positions_changed': True, 'synthetic_noise_added': False,
                'actual_points_added': 0, 'source_samples_created': 0,
                'input_items': input_count, 'unique_input_points': valid,
                'input_valid_points': valid, 'views': len(word),
                'mapped_instances': valid*len(word), 'output_points': valid*len(word),
                'input_reused_across_views': len(word)>1,
                'source_use': 'one observed signal independently mapped to each glyph',
                'source_time_shifted': False, 'ema_time_constant_s': 1.,
                'fluctuation_basis': 'fixed input XY, same EMA on both axes',
                'invalid_input_points': invalid, 'outside_word_points': outside,
                'origin': origin, 'full_stroke_width': 2*half_width,
                'start_ms': start_ms, 'end_ms': end_ms, 'window_ms': window_ms}
    return result, layout, metadata
