"""Single-flight controller. All device evidence comes from matching real ACKs."""
import json
import struct
import time
from tune_parameters import PARAMETERS, BY_ID, BY_NAME, RESTORE_FIELDS, validate
from tune_protocol import request

REASONS = {1: '不支持的命令', 2: '长度错误', 3: '过期请求ID', 4: '当前状态拒绝',
           5: '参数范围错误', 6: '未知参数', 7: '状态页错误'}


class Controller:
    def __init__(self, send, cancel=lambda: None, clock=time.monotonic, sample=lambda row: None):
        self.send, self.cancel, self.clock, self.sample = send, cancel, clock, sample
        self.connected = self.synced = False
        self.pending = self.confirmation = None
        self.deferred = None
        self.batch = None
        self.next_id = 1
        self.used_ids = set()
        self.read_fallback_id = 1
        self.status = None
        self.status_at = 0.0
        self.parameters = {}
        self.requests = {}
        self.telemetry = {}
        self.gesture_event_count = None
        self.note = '未连接；没有发送任何参数或运行命令'
        self.last_poll = 0.0
        self.poll_index = 0
        self.telemetry_poll_index = 0
        self.active_run_request = None
        self.stopping_run_request = None

    def opened(self):
        self.connected, self.synced = True, False
        self.status, self.parameters, self.telemetry = None, {}, {}
        self.gesture_event_count = None
        self._issue(1, b'\0', 'status', page=0)

    def closed(self, reason='连接已关闭；不会自动重新起摆'):
        self.cancel()
        for item in (self.pending, self.confirmation):
            if item and item.get('rid'):
                self._finish(item['rid'], 'indeterminate', reason)
        if self.active_run_request:
            self._finish(self.active_run_request, 'indeterminate', '断线，无法确认实际运动状态')
        if self.stopping_run_request:
            self._finish(self.stopping_run_request, 'indeterminate', '断线，无法确认实际已停止')
        self.active_run_request = None
        self.stopping_run_request = None
        if self.deferred:
            self._finish(self.deferred[0], 'cancelled', '断线取消待发操作；不会恢复')
        self.deferred = None
        if self.batch:
            self._finish(self.batch['rid'], 'cancelled', '断线取消剩余PID恢复；不重放')
        self.batch = None
        self.pending = self.confirmation = None
        self.connected = self.synced = False
        self.status = None
        self.parameters = {}
        self.note = reason

    def _id(self, readonly=False):
        if self.next_id > 0xFFFFFFFF:
            if not readonly:
                raise ValueError('写操作ID已用尽，不能回绕；STOP和状态读取仍然可用')
            while self.read_fallback_id in self.used_ids and self.read_fallback_id <= 0xFFFFFFFF:
                self.read_fallback_id += 1
            if self.read_fallback_id > 0xFFFFFFFF:
                raise ValueError('本次Host所有只读ID已用尽')
            cid = self.read_fallback_id
            self.read_fallback_id += 1
        else:
            cid = self.next_id
            self.next_id += 1
        self.used_ids.add(cid)
        return cid

    def _issue(self, op, payload, kind, rid=None, stop=False, cid=None, **extra):
        cid = self._id(readonly=op in (0, 1, 0x71, 0x72)) if cid is None else cid
        pending = dict(cid=cid, op=op, payload=payload, kind=kind, rid=rid,
                       created=self.clock(), sent_at=None, **extra)
        self.pending = pending
        try:
            self.send(request(cid, op, payload), cid, stop)
        except Exception:
            self.pending = None
            raise

    def sent(self, cid, at):
        if self.pending and self.pending['cid'] == cid:
            self.pending['sent_at'] = at

    def _finish(self, rid, state, message, **data):
        if rid in self.requests:
            self.requests[rid].update(state=state, message=message, **data)
        self.note = message

    def _fresh(self):
        if not self.connected or not self.synced or not self.status or self.clock()-self.status_at > 1.0:
            raise ValueError('需要最新的真实状态读回，请等待状态刷新')
        return self.status

    def _stopped(self, allow_fault=False):
        s = self._fresh()
        if s['pwm'] != 0 or not s['pwm_zero'] or s['run_session'] or s['swing_active'] or s['zero_capture'] or s['estop']:
            raise ValueError('必须先STOP并读回PWM=0、无运行会话和急停')
        if not allow_fault and (s['fault'] or not s['stopped_config_allowed']):
            raise ValueError('当前故障/停止状态不允许配置或启动')
        return s

    def submit(self, rid, action, name=None, value=None):
        if not isinstance(rid, str) or not 1 <= len(rid) <= 128:
            raise ValueError('request_id必须为1..128字符')
        fingerprint = json.dumps([action, name, value], ensure_ascii=False, separators=(',', ':'), sort_keys=True)
        old = self.requests.get(rid)
        if old:
            if old['fingerprint'] != fingerprint:
                raise ValueError('同一request_id不能用于不同请求')
            return dict(old)
        if len(self.requests) >= 10000 and action != 'stop':
            raise ValueError('本次Host请求记录已满，请停止并重新启动Host')
        record = dict(request_id=rid, fingerprint=fingerprint, action=action,
                      state='pending', message='等待真实ACK')
        self.requests[rid] = record
        self._start(rid, action, name, value)
        return dict(record)

    def _start(self, rid, action, name=None, value=None):
        try:
            if not self.connected:
                raise ValueError('尚未连接串口')
            if action == 'stop':
                self.cancel()
                if self.deferred:
                    self._finish(self.deferred[0], 'cancelled', 'STOP取消待发操作；不重放')
                self.deferred = None
                if self.batch:
                    self._finish(self.batch['rid'], 'cancelled', 'STOP取消剩余PID恢复；已写部分保留真实读回')
                self.batch = None
                for item in (self.pending, self.confirmation):
                    if item and item.get('rid'):
                        self._finish(item['rid'], 'cancelled', 'STOP取消了待发或待确认操作；不重放')
                self.pending = self.confirmation = None
                if self.active_run_request:
                    self.stopping_run_request = self.active_run_request
                    self._finish(self.active_run_request, 'stopping', '已请求STOP；等待真实停止读回')
                    self.active_run_request = None
                self._issue(0, b'', 'action', rid, stop=True, action=action)
            else:
                # One explicit intent may wait for an in-flight read, but never
                # for another mutation. It is revalidated before transmission.
                if self.pending and self.pending['kind'] in ('status', 'read') and not self.confirmation and not self.deferred and not self.batch:
                    self.deferred = (rid, action, name, value, self.clock())
                    self._finish(rid, 'queued', '等待当前只读查询完成；STOP可取消')
                    return
                if self.pending or self.confirmation or self.batch:
                    raise ValueError('正在等待ACK/状态；本次请求未排队，请完成后重新显式提交')
                if action == 'restore-pid':
                    self._stopped()
                    if not isinstance(value, dict) or set(value) != set(RESTORE_FIELDS):
                        raise ValueError('整组恢复必须明确提供6个PID增益、积分使能、原有角度D滤波和窗口')
                    for field, val in value.items():
                        validate(field, val)
                    self.batch = dict(rid=rid, remaining=[(field, value[field]) for field in RESTORE_FIELDS], confirmed=[])
                    self._finish(rid, 'accepted', '整组PID逐项写入并独立读回；STOP可取消剩余项，不会自动RUN')
                elif action == 'apply-preset':
                    s = self._stopped()
                    from tune_presets import validate_preset
                    preset = validate_preset(value)
                    for field, expected in preset.get('expected_readonly', {}).items():
                        actual = self.parameters.get(field)
                        if not actual or actual['revision'] != s['revision'] or actual['value'] != expected:
                            raise ValueError('预设所需只读配置尚未真实读回或不一致：'+field)
                    self.batch = dict(rid=rid, remaining=list(preset['parameters'].items()), confirmed=[])
                    self._finish(rid, 'accepted', '显式预设逐项写入并独立读回；不会自动标零、起摆或开启自适应')
                elif action == 'load-profile':
                    self._stopped()
                    from e68_profile import compile_profile
                    if not isinstance(value,dict) or set(value)!={'profile','control'} or type(value['control']) is not bool:
                        raise ValueError('需要完整配置及明确的实机资格选择')
                    compiled=compile_profile(value['profile'],control=value['control'])
                    sequence=[('adaptive_mode',0),('adaptive_profile',0)]
                    sequence+=list(compiled['fields_u32'].items())
                    sequence+=[('adaptive_profile',1 if value['control'] else 2)]
                    self.batch=dict(rid=rid,remaining=sequence,confirmed=[])
                    self._finish(rid,'accepted','配置逐项写入并独立读回；提交后仍关闭自适应，不会自动RUN')
                elif action == 'set':
                    spec = validate(name, value)
                    s = self._stopped()
                    other = 'wait_timeout_ms' if name == 'qualify_ms' else 'qualify_ms'
                    if name in ('qualify_ms', 'wait_timeout_ms'):
                        actual = self.parameters.get(other)
                        if not actual or actual['revision'] != s['revision']:
                            raise ValueError('请先等待读回等待时间与确认时间')
                        if (name == 'qualify_ms' and value > actual['value']) or (name == 'wait_timeout_ms' and value < actual['value']):
                            raise ValueError('下垂确认时间不能大于等待最长时间')
                    self._issue(0x70, struct.pack('<BI', spec[0], value), 'write', rid, param=spec[0], expected=value)
                elif action in ('run', 'zero', 'clear-fault', 'balance'):
                    s = self._stopped(allow_fault=action == 'clear-fault')
                    if action == 'clear-fault' and s['fault'] not in (1, 7):
                        raise ValueError('只有真实F01/F07允许请求清除')
                    if action in ('run', 'balance'):
                        if not s['zero_valid']:
                            raise ValueError('零点无效，先显式标定')
                        for field in RESTORE_FIELDS:
                            p = self.parameters.get(field)
                            if not p or p['revision'] != s['revision']:
                                raise ValueError('尚未完整读回当前PID及积分使能')
                        if self.parameters['angle_kp']['value'] == 0:
                            raise ValueError('冷启动角度P为0；先显式恢复已有PID并读回')
                    op = {'run': 2, 'zero': 3, 'clear-fault': 4, 'balance': 5}[action]
                    self._issue(op, b'', 'action', rid, action=action)
                else:
                    raise ValueError('未知操作')
        except Exception as exc:
            self._finish(rid, 'rejected', str(exc))

    def receive(self, frame, at=None):
        # Unsolicited telemetry must never acknowledge or complete a command.
        if frame.kind != 'ack':
            return False
        now = self.clock() if at is None else at
        p, v = self.pending, frame.values
        if not p or p['sent_at'] is None or now < p['sent_at'] or v['command_id'] != p['cid'] or v['opcode'] != p['op']:
            return False
        self.pending = None
        if v['status']:
            if p.get('rid'):
                self._finish(p['rid'], 'rejected', 'RTL拒绝：'+REASONS.get(v['reason'], str(v['reason'])))
            self.confirmation = None
            if self.batch and self.batch['rid'] == p.get('rid'):
                self.batch = None
            self.last_poll = 0.0
            return True
        data = bytes.fromhex(v['readback_hex'])
        try:
            if p['kind'] == 'status':
                self._status_page(p['page'], data, now)
            elif p['kind'] in ('read', 'verify', 'write'):
                if len(data) != 5 or data[0] != p['param']:
                    raise ValueError('参数ACK长度或ID不匹配')
                pid, actual = struct.unpack('<BI', data)
                spec = BY_ID[pid]
                cold_unconfigured_read = p['kind'] == 'read' and 0x40 <= pid <= 0x49 and actual == 0
                if not cold_unconfigured_read and not spec[5] <= actual <= spec[6]:
                    raise ValueError('实际参数超出约定范围')
                if p['kind'] in ('write', 'verify') and actual != p['expected']:
                    raise ValueError('真实参数读回与请求不一致')
                if p['kind'] == 'write':
                    self._issue(0x71, bytes([pid]), 'verify', p['rid'], param=pid,
                                expected=p['expected'], write_revision=v['revision'])
                else:
                    if p['kind'] == 'verify' and v['revision'] != p['write_revision']:
                        raise ValueError('独立读回期间配置版本发生变化')
                    self.parameters[spec[1]] = dict(value=actual, revision=v['revision'])
                    if p.get('rid'):
                        if self.batch and self.batch['rid'] == p['rid']:
                            self.batch['confirmed'].append(spec[1])
                            self._finish(p['rid'], 'accepted', f'配置已独立读回 {len(self.batch["confirmed"])} 项', confirmed=list(self.batch['confirmed']))
                        else:
                            self._finish(p['rid'], 'verified', '写入ACK及独立真实读回一致', value=actual, revision=v['revision'])
            else:
                if data:
                    raise ValueError('操作ACK必须为空')
                self.confirmation = dict(rid=p['rid'], action=p['action'], deadline=now+3.0)
                self._finish(p['rid'], 'accepted', '收到真实ACK；等待读回实际状态')
                self._issue(1, b'\0', 'status', page=0)
        except (ValueError, KeyError, struct.error) as exc:
            if p.get('rid'):
                self._finish(p['rid'], 'indeterminate', str(exc)+'；没有重发')
            self.note = str(exc)
            if self.batch and self.batch['rid'] == p.get('rid'):
                self.batch = None
        return True

    def _status_page(self, page, data, now):
        if len(data) != 16:
            raise ValueError('状态页必须是16字节')
        if page == 0:
            mode, fault, flags, state, pwm, retry, _, highwater, revision = struct.unpack('<BBBBhBBII', data)
            self.status = dict(mode=mode, fault=fault, flags=flags, swing_state=state,
                               pwm=pwm, retry=retry, last_mutating_id=highwater, revision=revision,
                               **{name: bool(flags & (1 << bit)) for bit, name in enumerate((
                                   'zero_valid', 'run_session', 'swing_active', 'swing_fail',
                                   'zero_capture', 'estop', 'stopped_config_allowed', 'pwm_zero'))})
            self.status_at, self.synced = now, True
            self.next_id = max(self.next_id, highwater+1)
            self._confirm(now)
            if self.stopping_run_request and pwm == 0 and self.status['pwm_zero'] and not self.status['run_session'] and not self.status['swing_active']:
                self._finish(self.stopping_run_request, 'stopped', '真实状态确认STOP后已停止')
                self.stopping_run_request = None
            if self.active_run_request:
                if fault or self.status['swing_fail']:
                    self._finish(self.active_run_request, 'failed', f'真实状态：起摆失败/F{fault:02X}')
                    self.active_run_request = None
                elif mode == 3:
                    self._finish(self.active_run_request, 'balance', '真实状态已进入BALANCE；不等于已验证长期稳定')
                elif self.status['swing_active']:
                    self._finish(self.active_run_request, 'swinging', '真实状态：正在起摆')
                elif not self.status['run_session']:
                    self._finish(self.active_run_request, 'stopped', '真实状态已停止')
                    self.active_run_request = None
        elif page == 1:
            stamp, alpha, alpha_dot, theta_dot = struct.unpack('<Iiii', data)
            row = dict(page=1, timestamp_ms=stamp, alpha_q20=alpha, alpha_dot_q20=alpha_dot, theta_dot_q20=theta_dot)
            self.telemetry['angle'] = row
            self.sample(dict(host_monotonic=now, **row))
        elif page == 2:
            stamp, encoder, pwm, mode, fault, flags, state, retry, _ = struct.unpack('<IihBBBBBB', data)
            row = dict(page=2, timestamp_ms=stamp, encoder_count=encoder, pwm=pwm,
                       mode=mode, fault=fault, flags=flags, swing_state=state, retry=retry)
            self.telemetry['arm'] = row
            self.sample(dict(host_monotonic=now, **row))
        elif page == 3:
            state, reason, elapsed, forward_ms, reverse_ms, stamp, retry, hold, flags, _ = struct.unpack('<BBHHHIBBBB', data)
            row = dict(page=3, timestamp_ms=stamp, swing_state=state,
                       phase_end_reason=reason, phase_elapsed_ms=elapsed,
                       effective_forward_ms=forward_ms, session_reverse_ms=reverse_ms,
                       retry=retry, handoff_hold_ms=hold, phase_flags=flags,
                       phase_active=bool(flags & 1), capture_candidate=bool(flags & 2),
                       handoff_confirmed=bool(flags & 4), phase_failed=bool(flags & 8))
            self.telemetry['phase'] = row
            self.sample(dict(host_monotonic=now, **row))

        elif page==4:
            self.telemetry['gesture']=dict(sensor_status=data[:8].hex(),motion_status=data[8:].hex())
        elif page==5:
            selected,effective,flags,fault,revision,stamp,sample_flags=struct.unpack('<BBBBIII',data)
            self.telemetry['adaptive']=dict(selected=selected,effective=effective,profile_flags=flags,
              fault=fault,profile_revision=revision,sample_ms=stamp,sample_flags=sample_flags)
        elif page==6:
            kp,ki,kd,stamp=struct.unpack('<IIII',data)
            self.telemetry['adaptive_gains']=dict(kp_q24=kp,ki_q24=ki,kd_q24=kd,sample_ms=stamp)
        elif page==7:
            self.telemetry['adaptive_integral']=dict(sum_q20=int.from_bytes(data[:12], 'little', signed=True),
                                                      sum_q20_decimal=str(int.from_bytes(data[:12], 'little', signed=True)),
                                                      sample_ms=int.from_bytes(data[12:], 'little'))
        elif page==8:
            count,stamp,code,action,reason,flags,event_id=struct.unpack('<IIBBBBI',data)
            changed = self.gesture_event_count is not None and count != self.gesture_event_count
            self.gesture_event_count = count
            self.telemetry['gesture_event']=dict(event_count=count,event_ms=stamp,code=code,action=action,
                reason=reason,event_flags=flags,event_id=event_id,new_since_poll=changed)
        elif page==9:
            sequence,stamp,errors,error,flags,state,mode,rotation,runtime=struct.unpack('<IIHBBBBBB',data)
            self.telemetry['gesture_runtime']=dict(poll_sequence=sequence,timestamp_ms=stamp,
                error_count=errors,error=error,flags=flags,swing_state=state,mode=mode,
                rotation_status=rotation,runtime=runtime,
                **{name:bool(flags&(1<<bit)) for bit,name in enumerate((
                    'ready','enabled','blocked','fresh_balance_ready','motor','zero','estop','fault'))},
                **{name:bool(runtime&(1<<bit)) for bit,name in enumerate((
                    'swing_active','handoff','auto_armed','manual_run','deadman','hold_initialized','rotation_active','busy'))})

    def _confirm(self, now):
        c, s = self.confirmation, self.status
        if not c:
            return
        action = c['action']
        off = s['pwm'] == 0 and s['pwm_zero'] and not s['run_session'] and not s['swing_active']
        passed = ((action == 'stop' and off) or
                  (action == 'clear-fault' and off and s['fault'] == 0) or
                  (action == 'zero' and s['zero_valid'] and not s['zero_capture']) or
                  (action == 'run' and (s['swing_active'] or s['mode'] == 3)) or
                  (action == 'balance' and s['mode'] == 3))
        if passed:
            self._finish(c['rid'], 'verified', '真实状态读回确认完成')
            if action in ('run', 'balance'):
                self.active_run_request = c['rid']
            self.confirmation = None
        elif action in ('run', 'balance') and (s['fault'] or s['swing_fail']):
            self._finish(c['rid'], 'failed', f'真实状态拒绝/失败：F{s["fault"]:02X}')
            self.confirmation = None
        elif now >= c['deadline']:
            self._finish(c['rid'], 'indeterminate', '状态确认超时；没有重发或再次起摆')
            self.confirmation = None

    def tick(self):
        now = self.clock()
        if not self.connected:
            return
        if self.pending:
            if now-self.pending['created'] > 3.0:
                p = self.pending
                self.pending = None
                self.cancel()
                if p.get('rid'):
                    self._finish(p['rid'], 'indeterminate', 'ACK超时，结果未知；没有重发')
                if self.batch and self.batch['rid'] == p.get('rid'):
                    self.batch = None
                elif self.batch and p['kind'] == 'status':
                    self._finish(self.batch['rid'], 'indeterminate', '状态查询超时；取消其余PID写入')
                    self.batch = None
                self.note = 'ACK超时，结果未知；只进行新的状态读取'
                self.last_poll = 0.0
            else:
                return
        if self.confirmation:
            if now >= self.confirmation['deadline']:
                self._finish(self.confirmation['rid'], 'indeterminate', '状态确认超时；没有重发')
                self.confirmation = None
            else:
                self._issue(1, b'\0', 'status', page=0)
                return
        if self.deferred:
            rid, action, name, value, created = self.deferred
            self.deferred = None
            if now-created > 3.0:
                self._finish(rid, 'cancelled', '等待只读查询超时，未发送操作')
            else:
                self._start(rid, action, name, value)
            return
        if self.batch:
            if not self.batch['remaining']:
                self._finish(self.batch['rid'], 'verified', '整组配置独立读回完成；不会自动RUN或开启自适应', confirmed=self.batch['confirmed'])
                self.batch = None
                return
            if now-self.status_at > 0.15:
                self._issue(1, b'\0', 'status', page=0)
                return
            try:
                self._stopped()
                name, value = self.batch['remaining'].pop(0)
                pid = BY_NAME[name][0]
                self._issue(0x70, struct.pack('<BI', pid, value), 'write', self.batch['rid'], param=pid, expected=value)
            except Exception as exc:
                self._finish(self.batch['rid'], 'rejected', str(exc)+'；其余PID未发送')
                self.batch = None
            return
        if now-self.last_poll < 0.045:
            return
        self.last_poll = now
        if not self.synced or now-self.status_at > 0.15:
            self._issue(1, b'\0', 'status', page=0)
            return
        stale = [p for p in PARAMETERS if self.parameters.get(p[1], {}).get('revision') != self.status['revision']]
        if stale and self.poll_index % 2 == 0:
            pid = stale[0][0]
            self._issue(0x71, bytes([pid]), 'read', param=pid)
        else:
            pages = (8, 9, 1, 8, 9, 2, 8, 9, 3, 4, 5, 6, 7)
            page = pages[self.telemetry_poll_index % len(pages)]
            self.telemetry_poll_index += 1
            self._issue(1, bytes([page]), 'status', page=page)
        self.poll_index += 1

    def snapshot(self):
        return dict(connected=self.connected, synced=self.synced, status=self.status,
                    status_age_s=None if not self.status else round(self.clock()-self.status_at, 3),
                    parameters=dict(self.parameters), telemetry=dict(self.telemetry),
                    pending=None if not self.pending else {k: self.pending[k] for k in ('cid', 'op', 'kind')},
                    note=self.note, requests={k: {x: y for x, y in v.items() if x != 'fingerprint'} for k, v in self.requests.items()})
