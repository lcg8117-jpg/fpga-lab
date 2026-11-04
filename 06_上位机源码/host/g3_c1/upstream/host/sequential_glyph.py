"""Causal, presentation-only sequential glyph geometry from new observed points.

The approved glyph outlines and explicit phase-driven circular carrier supply
the display shape. Actual XY samples modulate its bounded irregularity. These
coordinates are neither encoder positions nor inferred physical motion. Raw
input objects, their time order, and the original acquisition phases are intact.
"""
from bisect import bisect_right
from copy import deepcopy
import math

from word_visualization import _letter_paths, _path_length, word_layout


LETTERS = 'GOWIN'
SAMPLE_MS = 5
_LOOPS = dict(G=24, O=25, W=28, I=22, N=26)


def _integer(value, lower=0):
    if type(value) is not int or value < lower:
        raise ValueError('Expected an integer in range')
    return value


def _arc(path):
    distance = [0.0]
    for a, b in zip(path, path[1:]):
        distance.append(distance[-1]+math.hypot(b[0]-a[0], b[1]-a[1]))
    return distance


def _at(path, distance, progress):
    if progress <= 0:
        return path[0]
    if progress >= 1:
        return path[-1]
    where = distance[-1]*progress
    index = min(len(path)-2, bisect_right(distance, where)-1)
    fraction = (where-distance[index])/(distance[index+1]-distance[index])
    return tuple(a+(b-a)*fraction for a,b in zip(path[index],path[index+1]))


def build_layout(manifest):
    """Keep five-letter timings, or split one G/O display interval causally.

    All timing decisions precede acquisition. The single-G fallback allocates
    the G interval across five display glyphs and four transitions by fixed
    arclength; it does not turn one physical G task into five physical tasks.
    """
    if not isinstance(manifest, dict):
        raise ValueError('Expected a sequence manifest')
    window = _integer(manifest.get('window_ms'), 5)
    cpr = _integer(manifest.get('counts_per_rev'), 1)
    if window % SAMPLE_MS:
        raise ValueError('Window must use the 5 ms grid')
    phases = manifest.get('phases')
    if not isinstance(phases, list) or not phases:
        raise ValueError('Expected declared acquisition phases')
    end = 0
    seen = set()
    named = {}
    for phase in phases:
        if not isinstance(phase, dict):
            raise ValueError('Invalid phase')
        name = phase.get('name')
        start = _integer(phase.get('start_ms'))
        stop = _integer(phase.get('end_ms'), 5)
        if (not isinstance(name,str) or name in seen or start != end or stop <= start
                or start % SAMPLE_MS or stop % SAMPLE_MS):
            raise ValueError('Phases must be unique contiguous 5 ms intervals')
        seen.add(name)
        named[name] = dict(name=name,start_ms=start,end_ms=stop)
        end = stop
    if end > 600000:
        raise ValueError('Display task exceeds the existing finite task limit')
    # A tiny fixed layout request retrieves the already-approved common offsets;
    # no target samples from other views are copied or used as acquired points.
    approved = word_layout('GOWIN',start_ms=0,end_ms=50,
                           window_ms=min(window,150000),half_width=28)
    paths = _letter_paths()
    offsets = {v['letter']:v['offset_x'] for v in approved['letters']}
    geometry = []
    for index,letter in enumerate(LETTERS):
        path = [(x+offsets[letter],y) for x,y in paths[letter]]
        geometry.append(dict(name=letter,kind='letter',path=path,
                             length=_path_length(path),loops=_LOOPS[letter]))
        if index+1 < len(LETTERS):
            next_letter = LETTERS[index+1]
            endpoint = paths[next_letter][0]
            to = (endpoint[0]+offsets[next_letter],endpoint[1])
            length = math.dist(path[-1],to)
            geometry.append(dict(name=f'transition_{letter}_{next_letter}',kind='transition',
                                 path=[path[-1],to],length=length,
                                 loops=min(12,max(4,math.ceil(length/15)))))
    available = [letter for letter in LETTERS if letter in named]
    if available == list(LETTERS):
        ordered = sorted(LETTERS,key=lambda letter:named[letter]['start_ms'])
        if ordered != list(LETTERS):
            raise ValueError('Acquisition letters must follow G O W I N')
        for index,segment in enumerate(geometry):
            if segment['kind']=='letter':
                source = named[segment['name']]
                segment.update(start_ms=source['start_ms'],end_ms=source['end_ms'])
            else:
                left,right = LETTERS[(index-1)//2:(index-1)//2+2]
                segment.update(start_ms=named[left]['end_ms'],end_ms=named[right]['start_ms'])
                if segment['end_ms']-segment['start_ms'] < SAMPLE_MS:
                    raise ValueError('Sequential display needs sampled transition intervals')
        timing_source = 'existing_five_letter_acquisition_intervals'
    elif available in (['G'],['O']):
        single = available[0]
        start,stop = named[single]['start_ms'],named[single]['end_ms']
        slots = (stop-start)//SAMPLE_MS
        if slots < 2*len(geometry):
            raise ValueError('Single-letter display interval is too short for all segments')
        lengths = [segment['length'] for segment in geometry]
        remaining = slots-2*len(geometry)
        extras = [remaining*length/sum(lengths) for length in lengths]
        allocation = [2+math.floor(value) for value in extras]
        order = sorted(range(len(geometry)),key=lambda i:(-(extras[i]%1),i))
        for index in order[:slots-sum(allocation)]:
            allocation[index] += 1
        cursor = start
        for segment,count in zip(geometry,allocation):
            segment.update(start_ms=cursor,end_ms=cursor+count*SAMPLE_MS)
            cursor = segment['end_ms']
        assert cursor == stop
        timing_source = f'single_{single}_interval_split_for_display_only'
    else:
        raise ValueError('Expected a complete GOWIN acquisition or one G/O interval')
    for segment in geometry:
        if segment['end_ms'] <= segment['start_ms']:
            raise ValueError('Display interval cannot be empty')
        segment['distance'] = _arc(segment['path'])
    return dict(word='GOWIN',phases=geometry,start_ms=geometry[0]['start_ms'],
                end_ms=geometry[-1]['end_ms'],window_ms=window,counts_per_rev=cpr,
                width=approved['width'],y_limit=approved['y_limit'],full_width=56,
                bounds=[-approved['width']/2,approved['width']/2,-93,93],
                timing_source=timing_source,
                input_y_to_display=cpr*(window/1000)/(2*math.pi),
                original_phases=deepcopy(phases),presentation_only=True,
                actual_measurement=False)


class GlyphMapper:
    """Append at most one presentation point per distinct current source point."""

    def __init__(self, manifest):
        self._layout = build_layout(manifest)
        self._phases = self._layout['phases']
        self._ends = [p['end_ms'] for p in self._phases]
        self._points = []
        self._last_time = self._last_index = None
        self._mean = None
        self._last_raw = None
        self._pending_break = False
        self._counts = dict(input_points=0,output_points=0,rejected_points=0,
                            outside_display_points=0)

    @property
    def layout(self):
        return deepcopy(self._layout)

    @property
    def points(self):
        return deepcopy(self._points)

    @property
    def metadata(self):
        return dict(format='J280SequentialGlyphV1',word='GOWIN',
            presentation_only=True,actual_measurement=False,control_validation=False,
            positions_changed=True,raw_data_modified=False,source_time_shifted=False,
            input_reused_across_letters=False,source_samples_created=0,
            output_units='glyph_display_units',y_is_velocity=False,
            carrier_added=True,carrier_kind='explicit_phase_driven_circular_display_geometry',
            connector_kind='display_geometry_not_acquisition',synthetic_noise_added=False,
            fluctuation_source='causal_common_750ms_XY_EMA_residual_bounded_to_display',
            display_edge_texture_source='current_sample_differences',
            display_edge_texture='bounded adjacent valid raw XY changes; reset across gaps',
            display_edge_texture_max_radius=4.5,display_carrier_max_radius=23.5,
            display_total_offset_max_radius=28.,
            raw_y_conversion='y_rad_s * counts_per_rev * window_seconds / (2*pi)',
            continuous_without_new_samples=False,
            stationary_encoder_behavior='new_samples_advance_display_carrier; raw_coordinates_stay_fixed',
            timing_source=self._layout['timing_source'],full_stroke_width=56,
            start_ms=self._layout['start_ms'],end_ms=self._layout['end_ms'],
            **self._counts)

    def feed(self, point, break_before=False):
        """Map an already validated measured point; no clocks, I/O or look-ahead."""
        try:
            if not isinstance(point,dict) or type(break_before) is not bool:
                raise ValueError('Expected a point and a Boolean break flag')
            ms = _integer(point['relative_ms'])
            index = _integer(point['source_index'])
            x,y = point['x_count'],point['y_rad_s']
            if any(type(v) not in (int,float) or not math.isfinite(v) for v in (x,y)):
                raise ValueError('Expected finite observed coordinates')
            if ms % SAMPLE_MS or (self._last_time is not None and
                    (ms <= self._last_time or index <= self._last_index)):
                raise ValueError('Expected new time-ordered source samples')
            raw = (float(x),float(y)*self._layout['input_y_to_display'])
            if not all(math.isfinite(v) and abs(v)<=2**65 for v in raw):
                raise ValueError('Observed coordinates exceed finite arithmetic')
        except (KeyError,TypeError,ValueError,OverflowError):
            self._counts['rejected_points'] += 1
            self._pending_break = True
            return None
        gap = (self._last_time is not None and
               (ms-self._last_time != SAMPLE_MS or index-self._last_index != 1))
        interrupted = break_before or gap or self._pending_break
        if interrupted or self._mean is None:
            self._mean = raw
        # Quantized encoder/window differences supply short edges. This is a
        # display embellishment driven by adjacent samples, not added noise or
        # a claim that the motor itself produced these glyph coordinates.
        if interrupted or self._last_raw is None:
            edge_x = edge_y = 0.0
        else:
            change_x,change_y = (v-p for v,p in zip(raw,self._last_raw))
            change_radius = math.hypot(change_x,change_y)
            edge_factor = math.tanh(change_radius/1.25)/change_radius if change_radius else 0.
            edge_x,edge_y = change_x*edge_factor,change_y*edge_factor
        self._last_raw = raw
        alpha = -math.expm1(-SAMPLE_MS/750)
        self._mean = tuple(m+alpha*(v-m) for m,v in zip(self._mean,raw))
        dx,dy = (v-m for v,m in zip(raw,self._mean))
        radius = math.hypot(dx,dy)
        factor = math.tanh(radius/18)/radius if radius else 0.0
        ux,uy = dx*factor,dy*factor
        self._last_time,self._last_index = ms,index
        self._counts['input_points'] += 1
        self._pending_break = interrupted
        if not self._layout['start_ms'] <= ms < self._layout['end_ms']:
            self._counts['outside_display_points'] += 1
            return None
        phase = self._phases[bisect_right(self._ends,ms)]
        duration = phase['end_ms']-phase['start_ms']
        u = (ms-phase['start_ms'])/max(SAMPLE_MS,duration-SAMPLE_MS)
        if phase['kind']=='letter':
            cx,cy = _at(phase['path'],phase['distance'],u)
            base_radius = 22.0
        else:
            # The connector's carrier below is visible geometric looping, never
            # an invented acquired waveform or a straight unobserved jump.
            smooth = u*u*(3-2*u)
            cx,cy = tuple(a+(b-a)*smooth for a,b in zip(*phase['path']))
            base_radius = 16.0
        envelope = math.sin(math.pi/2*min(1,u/.06,(1-u)/.06))**2
        phase_angle = 2*math.pi*phase['loops']*u+.58*ux+.43*uy+.22*(.7*edge_x+.7*edge_y)
        loop_radius = min(24,base_radius*(1+.22*ux+.16*uy))
        offset_x = loop_radius*(.88+.18*uy)*math.cos(phase_angle)+2.5*ux
        offset_y = loop_radius*(.88-.18*ux)*math.sin(phase_angle)+2.5*uy
        # Reserve separate radial budgets so real one-count steps can retain
        # visible fine edges without exceeding the approved 28-unit half-width.
        carrier_radius = math.hypot(offset_x,offset_y)
        if carrier_radius > 23.5:
            offset_x *= 23.5/carrier_radius
            offset_y *= 23.5/carrier_radius
        offset_x += 4.5*edge_x
        offset_y += 4.5*edge_y
        output = dict(source_index=index,relative_ms=ms,display_phase=phase['name'],
                      source_phase=point.get('phase'),x_display=cx+envelope*offset_x,
                      y_display=cy+envelope*offset_y,
                      break_before=bool(not self._points or interrupted),
                      presentation_only=True,actual_measurement=False)
        self._points.append(output)
        self._counts['output_points'] += 1
        self._pending_break = False
        return dict(output)
