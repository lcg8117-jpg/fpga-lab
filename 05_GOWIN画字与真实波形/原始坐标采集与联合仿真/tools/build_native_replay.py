"""Independently audit completed native feedback and create a causal replay input.

This script never starts a simulator or reads an active final trace. The final
execution receipt is a prerequisite. Firmware reference, physical plant output,
sampled feedback and display coordinates remain distinct throughout.
"""
from pathlib import Path
import collections
import csv
import hashlib
import json
import math
import statistics
import sys
import argparse

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--case',type=Path,default=ROOT/'cosim/cases/sequence_r03')
parser.add_argument('--output',type=Path,default=ROOT/'results')
options = parser.parse_args()
CASE = options.case.resolve()
OUT = options.output.resolve()
sys.path.insert(0, str(ROOT/'host'))
sys.path.insert(0, str(ROOT/'work/host'))
from measured_sequence import SequenceTrace, reconstruct
from draw_trajectory import checksum


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for part in iter(lambda:stream.read(1024*1024), b''):
            h.update(part)
    return h.hexdigest()


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2,
                               allow_nan=False),encoding='utf-8')


def stat(values):
    if not values:
        return None
    return dict(n=len(values),minimum=min(values),maximum=max(values),
                mean=statistics.fmean(values),
                rms=math.sqrt(statistics.fmean(v*v for v in values)),
                peak_to_peak=max(values)-min(values))


def integer(value):
    value=float(value)
    assert math.isfinite(value) and value.is_integer(),value
    return int(value)


def main():
    receipt_path=CASE/'EXECUTION_RECEIPT.json'
    receipt=read_json(receipt_path)
    if receipt.get('status')!='PASS_EXECUTION_ONLY':
        raise SystemExit('WAIT: final execution receipt is not PASS_EXECUTION_ONLY; no final CSV read')
    assert receipt['matlab_exit_code']==receipt['modelsim_exit_code']==0
    freeze=read_json(CASE/'MODEL_FREEZE.json')
    assert digest(CASE/'MODEL_FREEZE.json')==receipt['freeze_sha256']
    assert digest(CASE/'RAW_TRACE.mat')==receipt['trace_sha256']
    for name,expected in freeze['files'].items():
        assert digest(CASE/name)==expected,name
    plan_path=CASE/'TRIAL_INPUT.json'
    assert digest(plan_path)==freeze['input_plan_sha256']
    plan=read_json(plan_path)
    meta=plan['word_metadata']
    assert meta['window_ms']==46000
    assert checksum(plan['points'],1)==meta['checksum']
    hex_points=[int(line,16) for line in (CASE/'program.hex').read_text().splitlines()]
    assert hex_points==[p&0xffff for p in plan['points']]
    source=CASE/'FULL_1KHZ.csv'
    before=digest(source)
    rows=[]
    with source.open(encoding='utf-8',newline='') as stream:
        for raw in csv.DictReader(stream):
            row={k:float(v) for k,v in raw.items()}
            assert all(math.isfinite(v) for v in row.values())
            ms=round(row['time_s']*1000)
            assert abs(ms-row['time_s']*1000)<1e-6
            row['ms']=ms
            if rows:
                assert ms==rows[-1]['ms']+1
            assert integer(row['completed_transactions'])==ms+1
            rows.append(row)
    assert digest(source)==before
    first=next((r for r in rows if r['motion_state']==6),None)
    assert first is not None,'No native WAVE'
    onset=first['ms']
    phases=[{k:p[k] for k in ('name','start_ms','end_ms')}
            for p in plan['sequence_metadata']['phases']]
    manifest=dict(counts_per_rev=1040,window_ms=meta['window_ms'],play_ms=onset,origin=0,
                  request_id=1,session_id='native_sequence_r03',source_kind='conditional_model',
                  phases=phases)
    selected=[r for r in rows if 0<=r['ms']-onset<phases[-1]['end_ms'] and (r['ms']-onset)%5==0]
    frames=[dict(board_ms=r['ms'],control_epoch=integer(r['completed_transactions']),
                 encoder=integer(r['frame_actual']),fault=integer(r['fault']),coherent=True,
                 request_id=1,session_id='native_sequence_r03') for r in selected]
    trace=SequenceTrace(manifest)
    for frame in frames:
        trace.feed(frame)
    rebuilt=reconstruct(trace.export())
    assert rebuilt.points==trace.points and rebuilt.breaks==trace.breaks
    points=trace.points
    time_index={r['ms']:r for r in selected}
    for p in points:
        current=time_index[p['board_ms']]
        past=time_index[p['board_ms']-meta['window_ms']]
        assert p['encoder']==current['frame_actual']
        y=(current['frame_actual']-past['frame_actual'])*2*math.pi/(1040*46)
        assert p['y_rad_s']==y
    target=plan['target_only_xy']
    start,end=meta['word_start_ms'],meta['word_end_ms']
    writing=[p for p in points if start<=p['relative_ms']<end]
    reference_grid={start+i*5:xy for i,xy in enumerate(target)}
    errors_x=[p['x_count']-reference_grid[p['relative_ms']][0] for p in writing]
    errors_y=[p['y_rad_s']-reference_grid[p['relative_ms']][1] for p in writing]
    scale=2*math.pi/(1040*46)
    distance=[math.hypot(x,y/scale) for x,y in zip(errors_x,errors_y)]
    ordered=sorted(distance)
    p95=ordered[max(0,math.ceil(.95*len(ordered))-1)] if ordered else None
    letters={p['name'] for p in plan['sequence_metadata']['phases'] if p['kind']=='letter'}
    memberships={name:[p['source_index'] for p in points if p['phase']==name] for name in letters}
    indices=[i for name in letters for i in memberships[name]]
    assert len(indices)==len(set(indices))
    assert all(a<b for a,b in zip([p['source_index'] for p in points],
                                 [p['source_index'] for p in points][1:]))
    ranges={name:dict(count=len(v),first=min(v) if v else None,last=max(v) if v else None)
            for name,v in memberships.items()}
    by_relative={p['relative_ms']:p for p in points}
    transitions=[]
    for phase in phases[1:]:
        ms=phase['start_ms']
        a,b=by_relative.get(ms-5),by_relative.get(ms)
        ra,rb=time_index.get(onset+ms-5),time_index.get(onset+ms)
        transitions.append(dict(boundary_ms=ms,next_phase=phase['name'],
            feedback_delta_count=None if not(ra and rb) else rb['frame_actual']-ra['frame_actual'],
            reference_delta_count=None if not(ra and rb) else rb['frame_target']-ra['frame_target'],
            velocity_delta_rad_s=None if not(a and b) else b['y_rad_s']-a['y_rad_s'],
            coordinate_offset_applied=False))
    tail=[r for r in rows if round(meta['total_s']*1000)<=r['ms']-onset<=phases[-1]['end_ms']]
    writing_1k=[r for r in rows if start<=r['ms']-onset<end]
    last=rows[-1]
    # Actual serial packets were not generated by this native harness. The CRC
    # here binds inert input RAM; it is not evidence of a live UART transfer.
    provenance=dict(source_kind='conditional_model',model=freeze['model']['name'],
        source_csv=str(source.relative_to(ROOT) if source.is_relative_to(ROOT) else source).replace('\\','/'),source_csv_sha256=before,
        source_freeze_sha256=receipt['freeze_sha256'],raw_mat_sha256=receipt['trace_sha256'],
        input_plan_sha256=digest(plan_path),input_ram_checksum=meta['checksum'],
        native_receipt_sha256=digest(receipt_path),
        source_field='frame_actual',reference_used_as_feedback=False,source_sample_ms=1,
        retained_sample_ms=5,retained_time_rule='first WAVE-relative multiples of5ms',
        board_ms_semantics='native physical simulation milliseconds, not a hardware clock reading',
        control_epoch_semantics='actual completed RTL fast transaction count',
        request_id_semantics='offline replay task identifier1; no hardware PLAY ACK is claimed',
        coherent_semantics='native transaction phase/finite-field checks passed; not a UART coherence flag',
        serial_frame_crc_tested=False,physical_acceptance=False,new_hardware_actions=False,
        current_samples_reused_across_letters=False,display_geometry_added=False,
        endpoint_calculation='exact sampled frame_actual at t and t-46000ms',
        model_scope=freeze['model']['provenance'])
    record=dict(format='J280SequentialInputV1',manifest=manifest,frames=frames,
        display_bounds=[-128,128,-48,48],display_y_scale=1/scale,
        display_y_bounds_unit='count_difference_after_fixed_scale',provenance=provenance)
    metric=dict(status='PASS_DATA_AUDIT_NOT_FUNCTIONAL_ACCEPTANCE',native_status=receipt['status'],
        native_rows=len(rows),first_wave_ms=onset,last_time_s=last['time_s'],
        counts=trace.counts,viewer_status=trace.status,points=len(points),
        phase_points=dict(collections.Counter(p['phase'] for p in points)),
        letter_source_ranges=ranges,current_letter_source_indices_disjoint=True,
        unique_current_source_indices=True,history_endpoints_may_be_reused=True,
        writing_points=len(writing),expected_writing_points=len(target),
        full_writing_and_tail=len(writing)==len(target) and rows[-1]['ms']-onset>=phases[-1]['end_ms']-5,
        x_error=stat(errors_x),y_error=stat(errors_y),canonical_xy_distance=stat(distance),
        paired_xy_p95_canonical_count=p95,
        tracking_error_count=stat([r['frame_actual']-r['frame_target'] for r in writing_1k]),
        tail_position=stat([r['frame_actual'] for r in tail]),
        tail_tracking_error=stat([r['frame_actual']-r['frame_target'] for r in tail]),
        fault_samples=sum(r['fault']!=0 for r in rows),
        maximum_outer_fault=max(r['outer_fault_code'] for r in rows),
        maximum_abs_pendulum_joint_angle_rad=max(abs(r['physical_theta_now']) for r in rows),
        maximum_abs_pin_snapshot_q15=max(abs(r['applied_snapshot_q15']) for r in rows),
        entire_position=stat([r['frame_actual'] for r in rows]),
        final_actual=last['frame_actual'],final_reference=last['frame_target'],
        final_strict_settled=bool(last['tracking_settled']),
        final_motion_state=integer(last['motion_state']),
        phase_boundary_sample_deltas=transitions,export_reconstruct_exact=True,
        raw_mat_receipt_bound=True,plan_checksum_verified=True,program_hex_exact=True,
        source_unchanged=True,physical_acceptance=False,approved_style_acceptance=False,
        input_source=provenance)
    OUT.mkdir(exist_ok=True)
    output=OUT/'NATIVE_R03_SEQUENTIAL_REPLAY.json'
    output.write_text(json.dumps(record,ensure_ascii=False,separators=(',',':'),allow_nan=False),encoding='utf-8')
    metric['replay_file']=output.name
    metric['replay_sha256']=digest(output)
    metric['replay_bytes']=output.stat().st_size
    save(OUT/'NATIVE_R03_INDEPENDENT_METRICS.json',metric)
    with (OUT/'NATIVE_R03_WRITING_XY.csv').open('w',encoding='utf-8',newline='') as stream:
        writer=csv.writer(stream)
        writer.writerow(['relative_ms','phase','source_index','actual_x_count','actual_y_rad_s',
                         'reference_x_count','reference_y_rad_s'])
        for p in writing:
            writer.writerow([p['relative_ms'],p['phase'],p['source_index'],p['x_count'],p['y_rad_s'],
                             *reference_grid[p['relative_ms']]])
    print(json.dumps({k:metric[k] for k in ('status','native_rows','writing_points','viewer_status',
        'x_error','y_error','fault_samples','final_strict_settled','replay_bytes')},ensure_ascii=False))


if __name__=='__main__':
    main()
