"""Incremental, unfiltered delay coordinates for one finite acquisition task.

Manifest: counts_per_rev, window_ms, play_ms, signed origin, positive request_id,
ASCII session_id, declared source_kind, and contiguous phases containing name,
start_ms and end_ms. Phase times are relative to play_ms. All times use a 5 ms
grid. Source kinds are physical_log, conditional_model and synthetic_test.

Frames contain board_ms, positive control_epoch, signed encoder, session_id,
request_id, fault (0..255), and coherent (bool). Board/epoch counters must not
wrap in this task. The first valid frame establishes their common time anchor;
later increments must match. Duplicate, reversed and inconsistent frames cannot
change that anchor. Identity changes, faults and cancellation permanently stop
this instance. New acquisitions require a new instance.

Every returned point is from one current frame and its exact t-window endpoint.
Accepted raw frames are retained for offline reconstruction; the working delay
index retains at most window_ms/5+1 frames. No GUI, transport or clock is used.
"""
from bisect import bisect_right
from collections import deque
from copy import deepcopy
import math
import re


SAMPLE_MS = 5
MAX_TASK_MS = 600000
MAX_PHASES = 64
UINT32_MAX = 2**32-1
INT64_MIN, INT64_MAX = -2**63, 2**63-1
FORMAT = 'J280MeasuredSequenceV1'
SOURCE_KINDS = ('physical_log', 'conditional_model', 'synthetic_test')
_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z')
_PHASE = re.compile(r'[A-Za-z][A-Za-z0-9_]{0,63}\Z')
_FRAME_FIELDS = ('board_ms', 'control_epoch', 'encoder', 'session_id',
                 'request_id', 'fault', 'coherent')


def _integer(value, low, high, label):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'Invalid {label}')
    return value


def _session(value):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError('Invalid session_id')
    return value


def _manifest(value):
    if not isinstance(value, dict):
        raise ValueError('Manifest must be an object')
    try:
        result = dict(
            counts_per_rev=_integer(value['counts_per_rev'], 1, 10**9, 'counts_per_rev'),
            window_ms=_integer(value['window_ms'], 5, MAX_TASK_MS, 'window_ms'),
            play_ms=_integer(value['play_ms'], 0, UINT32_MAX, 'play_ms'),
            origin=_integer(value['origin'], INT64_MIN, INT64_MAX, 'origin'),
            request_id=_integer(value['request_id'], 1, UINT32_MAX, 'request_id'),
            session_id=_session(value['session_id']), source_kind=value['source_kind'])
        if result['source_kind'] not in SOURCE_KINDS or result['window_ms'] % SAMPLE_MS:
            raise ValueError('Invalid source kind or window cadence')
        phases = value['phases']
        if not isinstance(phases, list) or not 1 <= len(phases) <= MAX_PHASES:
            raise ValueError('Expected 1..64 finite phases')
        result['phases'] = []
        previous = 0
        names = set()
        for phase in phases:
            name = phase['name']
            if not isinstance(name, str) or not _PHASE.fullmatch(name) or name in names:
                raise ValueError('Phase names must be unique ASCII identifiers')
            start = _integer(phase['start_ms'], 0, MAX_TASK_MS, 'phase start')
            end = _integer(phase['end_ms'], 5, MAX_TASK_MS, 'phase end')
            if start != previous or end <= start or start % SAMPLE_MS or end % SAMPLE_MS:
                raise ValueError('Phases must be contiguous, increasing 5 ms intervals')
            result['phases'].append(dict(name=name, start_ms=start, end_ms=end))
            previous = end
            names.add(name)
        if result['window_ms'] >= previous or result['play_ms']+previous > UINT32_MAX:
            raise ValueError('Task must exceed its delay window and avoid board wrap')
        return result
    except (KeyError, TypeError) as exc:
        raise ValueError('Missing or malformed manifest fields') from exc


def _frame(value):
    if not isinstance(value, dict):
        raise ValueError('Frame must be an object')
    try:
        frame = {key:value[key] for key in _FRAME_FIELDS}
        _integer(frame['board_ms'], 0, UINT32_MAX, 'board_ms')
        _integer(frame['control_epoch'], 1, UINT32_MAX, 'control_epoch')
        _integer(frame['encoder'], INT64_MIN, INT64_MAX, 'encoder')
        _integer(frame['request_id'], 1, UINT32_MAX, 'request_id')
        _integer(frame['fault'], 0, 255, 'fault')
        _session(frame['session_id'])
        if type(frame['coherent']) is not bool:
            raise ValueError('coherent must be bool')
        return frame
    except KeyError as exc:
        raise ValueError('Missing frame fields') from exc


class SequenceTrace:
    """One fixed-origin session; feed returns one measured point or None."""

    def __init__(self, manifest):
        self._manifest = _manifest(manifest)
        self._ends = [p['end_ms'] for p in self._manifest['phases']]
        self.status = 'waiting'
        self.phase = None
        self._points, self._breaks, self._frames = [], [], []
        self._history, self._order = {}, deque()
        self._last = None
        self._terminal = None
        self._pending_breaks = set()
        self.counts = dict(accepted_frames=0, points=0, missing_endpoints=0,
                           duplicates=0, reversed=0, epoch_mismatch=0,
                           incoherent=0, invalid_frames=0, foreign_identity=0,
                           out_of_range=0, missing_frames=0, faults=0, after_terminal=0)

    @property
    def manifest(self):
        return deepcopy(self._manifest)

    @property
    def points(self):
        return deepcopy(self._points)

    @property
    def point_count(self):
        return len(self._points)

    def points_since(self, index):
        """Copy only the new suffix; returned dictionaries never alias state."""
        _integer(index, 0, len(self._points), 'point index')
        return deepcopy(self._points[index:])

    @property
    def breaks(self):
        return deepcopy(self._breaks)

    @property
    def history_size(self):
        return len(self._history)

    def feed(self, frame):
        if self.status in ('complete', 'fault', 'cancelled', 'invalidated'):
            self.counts['after_terminal'] += 1
            return None
        try:
            frame = _frame(frame)
        except (ValueError, TypeError, OverflowError):
            self.counts['invalid_frames'] += 1
            return None
        if (frame['session_id'] != self._manifest['session_id'] or
                frame['request_id'] != self._manifest['request_id']):
            self.counts['foreign_identity'] += 1
            self.status = 'invalidated'
            self._terminal = dict(kind='identity_change', frame=frame)
            return None
        if not frame['coherent']:
            self.counts['incoherent'] += 1
            return None
        ms, epoch = frame['board_ms'], frame['control_epoch']
        relative = ms-self._manifest['play_ms']
        if not 0 <= relative < self._ends[-1] or relative % SAMPLE_MS:
            self.counts['out_of_range'] += 1
            return None
        gap = 0
        if self._last is not None:
            delta = ms-self._last['board_ms']
            if delta <= 0:
                self.counts['duplicates' if delta == 0 else 'reversed'] += 1
                return None
            if epoch-self._last['control_epoch'] != delta:
                self.counts['epoch_mismatch'] += 1
                return None
            gap = delta//SAMPLE_MS-1
        if frame['fault']:
            self.counts['faults'] += 1
            self.status = 'fault'
            self._terminal = dict(kind='fault', frame=frame)
            return None
        if gap:
            self.counts['missing_frames'] += gap
            self._pending_breaks.add('missing_frames')
        self._last = frame
        self._frames.append(frame)
        self.counts['accepted_frames'] += 1
        self.phase = self._manifest['phases'][bisect_right(self._ends, relative)]['name']
        self.status = 'complete' if relative == self._ends[-1]-SAMPLE_MS else 'running'
        cutoff = ms-self._manifest['window_ms']
        while self._order and self._order[0] < cutoff:
            del self._history[self._order.popleft()]
        self._history[ms] = (frame['encoder'], epoch)
        self._order.append(ms)
        delayed = self._history.get(cutoff)
        if delayed is None or epoch-delayed[1] != self._manifest['window_ms']:
            self.counts['missing_endpoints'] += 1
            if self._points:
                self._pending_breaks.add('missing_endpoint')
            return None
        if self._points and self._pending_breaks:
            self._breaks.append(dict(before_point_index=len(self._points), board_ms=ms,
                                     reasons=sorted(self._pending_breaks)))
        self._pending_breaks.clear()
        point = dict(board_ms=ms, relative_ms=relative, control_epoch=epoch,
                     source_index=len(self._frames)-1, phase=self.phase,
                     encoder=frame['encoder'], delayed_encoder=delayed[0],
                     x_count=frame['encoder']-self._manifest['origin'],
                     y_rad_s=(frame['encoder']-delayed[0])*2*math.pi /
                             (self._manifest['counts_per_rev']*(self._manifest['window_ms']/1000)))
        self._points.append(point)
        self.counts['points'] += 1
        return dict(point)

    def cancel(self, reason='operator_cancel'):
        """Lock the instance without advancing acquisition time or phase."""
        if not isinstance(reason, str) or not 1 <= len(reason) <= 160:
            raise ValueError('Expected a short cancellation reason')
        if self.status not in ('complete', 'fault', 'cancelled', 'invalidated'):
            self.status = 'cancelled'
            self._terminal = dict(kind='cancel', reason=reason)

    def export(self):
        """Raw accepted frames reconstruct all points; ignored totals are audit only."""
        return dict(format=FORMAT, manifest=self.manifest,
                    declared_source_kind=self._manifest['source_kind'],
                    physical_control_verified=False, accepted_frames=deepcopy(self._frames),
                    terminal_event=deepcopy(self._terminal), points=self.points,
                    breaks=self.breaks, status=self.status, phase=self.phase,
                    counts=dict(self.counts), ignored_frame_details_retained=False,
                    formula='x=ENC-origin; y=(ENC(t)-ENC(t-T))*2*pi/(CPR*T_seconds)')


def reconstruct(exported):
    """Rebuild points/breaks/state from accepted raw frames, rejecting altered plots.

    Ignored-frame counters cannot be rederived because rejected payloads are not
    retained; their exported totals remain audit metadata, not accepted samples.
    """
    if not isinstance(exported, dict) or exported.get('format') != FORMAT:
        raise ValueError('Unsupported measured-sequence export')
    try:
        trace = SequenceTrace(exported['manifest'])
        frames = exported['accepted_frames']
        if not isinstance(frames, list) or len(frames) > MAX_TASK_MS//SAMPLE_MS:
            raise ValueError('Invalid accepted-frame collection')
        for frame in frames:
            before = trace.counts['accepted_frames']
            trace.feed(frame)
            if trace.counts['accepted_frames'] != before+1:
                raise ValueError('Export contains a rejected accepted frame')
        terminal = exported['terminal_event']
        if terminal is not None:
            if terminal['kind'] == 'cancel':
                trace.cancel(terminal['reason'])
            elif terminal['kind'] in ('fault', 'identity_change'):
                trace.feed(terminal['frame'])
            else:
                raise ValueError('Invalid terminal event')
        if any(exported[key] != getattr(trace, key) for key in ('points', 'breaks', 'status', 'phase')):
            raise ValueError('Exported result does not match its raw frames')
        return trace
    except (KeyError, TypeError) as exc:
        raise ValueError('Malformed measured-sequence export') from exc
