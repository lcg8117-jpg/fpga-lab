"""SG21/SA21 loopback client and display adapter. Never owns a serial port."""
from __future__ import annotations

import json
import math
import re
import urllib.error
import urllib.parse
import urllib.request
from sp24_telemetry import PreciseTelemetryAdapter
import fd2_telemetry


class BridgeError(ValueError):
    pass


IDENTITIES = {('53473231', '53475421'): 'SG21', ('53413231', '53415421'): 'SA21',
              ('53503234', '53505424'): 'SP24'}
PID_NAMES = ('angle_kp','angle_kd','angle_ki','position_kp','position_kd','position_ki',
             'ki_enable','angle_d_filter','angle_window')
PARAMETER_BOUNDS = {
    **{name:(0,65535) for name in PID_NAMES[:6]},
    'ki_enable':(0,1), 'angle_d_filter':(0,4096), 'angle_window':(1,8),
    'forward_percent':(1,90), 'reverse_percent':(1,90), 'forward_ms':(1,1000), 'reverse_ms':(1,1000),
    'qualify_ms':(1,1000), 'wait_timeout_ms':(1,10000), 'coast_ms':(20,10000),
    'max_attempts':(1,10), 'retry_step_ms':(0,50), 'gesture_enable':(0,1),
    'adaptive_mode':(0,2), 'adaptive_integral':(0,1),
    'move_degrees':(1,360), 'left_negative':(0,1),
}
OBSERVATIONS = ('angle_deg','angle_velocity_deg_s','arm_angle_deg','arm_velocity_deg_s',
                'encoder','duty_actual_percent','position_error','position_target','alpha_ref_deg',
                'control_raw_percent','duty_command_percent','saturation')


def number(value):
    return type(value) in (int,float) and math.isfinite(value)


def integer(value, low, high):
    return value if type(value) is int and low <= value <= high else None


def identify(snapshot, expected='auto'):
    if not isinstance(snapshot,dict):
        raise BridgeError('Host 返回的数据不是对象')
    if snapshot.get('protocol') == 'FD2':
        try:
            variant=fd2_telemetry.identify(snapshot)
        except ValueError as exc:
            raise BridgeError(str(exc)) from exc
    else:
        variant=IDENTITIES.get((snapshot.get('user_code'),snapshot.get('schema')))
    if variant is None:
        raise BridgeError('Host 固件身份或 schema 不受支持，请使用匹配的 V1～V5 Host')
    if expected != 'auto' and variant != expected:
        raise BridgeError(f'预期 {expected}，当前 Host 为 {variant}')
    return variant


def validate_config(config):
    if set(config)-{'bridge_port','firmware_variant'}:
        raise BridgeError('配置仅接受 bridge_port 和 firmware_variant')
    port=config.get('bridge_port',8371)
    variant=config.get('firmware_variant','auto')
    if integer(port,1024,65535) is None or variant not in ('auto','V1','V2','V3','V4','V5','ADB1','SG21','SA21','SP24',*fd2_telemetry.DELIVERY_PROFILES):
        raise BridgeError('本机桥端口须为 1024..65535，版本须为 auto / V1～V5')
    return dict(bridge_port=port,firmware_variant=variant)


def validate_action(body,snapshot,expected='auto'):
    if not isinstance(body,dict) or set(body)-{'action','request_id','name','value','session','stroke_id','seq','target','issued_ms'}:
        raise BridgeError('命令字段无效')
    action=body.get('action')
    variant=identify(snapshot,'auto' if action in ('stop','estop') else expected)
    if snapshot.get('protocol') == 'FD2':
        try:
            return fd2_telemetry.validate_action(body,snapshot)
        except ValueError as exc:
            raise BridgeError(str(exc)) from exc
    if set(body)-{'action','request_id','name','value'}:
        raise BridgeError('命令字段无效')
    if action not in ('stop','run','zero','clear-fault','balance','set','restore-pid','load-profile','apply-preset'):
        raise BridgeError('操作不受支持；串口连接请使用配套 Host')
    rid=body.get('request_id')
    if not isinstance(rid,str) or not 1 <= len(rid) <= 128 or not re.fullmatch(r'[A-Za-z0-9_.:-]+',rid):
        raise BridgeError('每次显式操作需要唯一的 request_id（1..128 个字母、数字或 ._:-）')
    def check_value(name,value):
        bounds=PARAMETER_BOUNDS.get(name)
        if (bounds is None or (name.startswith('adaptive_') and variant not in ('SA21','SP24')) or
                (name in ('move_degrees','left_negative') and variant!='SP24')):
            raise BridgeError('当前固件不支持该可写参数')
        if integer(value,*bounds) is None:
            raise BridgeError(f'{name} 须为 {bounds[0]}..{bounds[1]} 的整数')
    if action=='set':
        if set(body)!={'action','request_id','name','value'}:
            raise BridgeError('set 需要 name 和 value')
        check_value(body['name'],body['value'])
    elif action=='restore-pid':
        values=body.get('value')
        if set(body)!={'action','request_id','value'} or not isinstance(values,dict) or set(values)!=set(PID_NAMES):
            raise BridgeError('整组 PID 需要六个增益、积分使能、D 滤波和角度窗口共九项明确数值')
        for name,value in values.items(): check_value(name,value)
    elif action=='load-profile':
        value=body.get('value')
        if variant not in ('SA21','SP24') or set(body)!={'action','request_id','value'} or not isinstance(value,dict) or set(value)!={'profile','control'}:
            raise BridgeError('自适应配置导入只支持 SA21 / SP24，需提供 profile 和 control')
        if not isinstance(value['profile'],dict) or type(value['control']) is not bool:
            raise BridgeError('配置必须为 JSON 对象，control 必须为布尔值')
        # Numerical/provenance qualification is performed by the delivered Host.
    elif action=='apply-preset':
        value=body.get('value')
        if variant!='SP24' or set(body)!={'action','request_id','value'} or not isinstance(value,dict):
            raise BridgeError('参数预设仅支持 SP24，需提供完整预设 JSON')
        if (value.get('schema') not in ('J280.SP24.fixed-preset.v1','J280.SP24.swing-preset.v1') or
                value.get('firmware_uid_hex')!='53503234' or value.get('control_schema_hex')!='53505424' or
                value.get('no_RUN_ZERO_or_profile_commit') is not True or
                not isinstance(value.get('parameters'),dict) or not value['parameters']):
            raise BridgeError('预设格式、固件身份或显式操作声明无效')
        for name,value in value['parameters'].items():
            check_value(name,value)
            if name=='adaptive_integral' or (name=='adaptive_mode' and value!=0):
                raise BridgeError('预设不能开启自适应')
        # Original Host enforces stopped state, read-only prerequisites and every ACK/readback.
    elif set(body)!={'action','request_id'}:
        raise BridgeError('该动作不接受额外参数')
    return body


class HostClient:
    def __init__(self,port=8371,timeout=1.0):
        validate_config({'bridge_port':port})
        self.base=f'http://127.0.0.1:{port}'
        self.port=port
        self.timeout=timeout
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def exchange(self,path,body=None):
        try:
            raw=None if body is None else json.dumps(body,ensure_ascii=False,allow_nan=False).encode('utf-8')
            if raw is not None and len(raw)>4096:
                raise BridgeError('命令 JSON 超过配套 Host 的 4096 字节限制')
            request=urllib.request.Request(self.base+path,data=raw,headers={
                'X-J280-Local':'1','Content-Type':'application/json','Accept':'application/json'})
            with self.opener.open(request,timeout=self.timeout) as response:
                content=response.read(2_000_001)
            if len(content)>2_000_000:
                raise BridgeError('Host 响应过大')
            value=json.loads(content)
            if not isinstance(value,dict): raise BridgeError('Host 返回的数据不是对象')
            return value
        except urllib.error.HTTPError as exc:
            code=None
            try:
                response=json.loads(exc.read(4096))
                detail=response.get('error',str(exc));code=response.get('code')
            except (ValueError,AttributeError): detail=str(exc)
            finally: exc.close()
            error = BridgeError(f'Host 拒绝请求：{detail}')
            error.request_rejected = exc.code in (400, 403, 404, 409, 422)
            if isinstance(code,str): error.code=code
            raise error from exc
        except (OSError,ValueError) as exc:
            if isinstance(exc,BridgeError): raise
            raise BridgeError(f'配套 Host 无响应或返回无效数据：{exc}') from exc

    def status(self): return self.exchange('/status')
    def command(self,body): return self.exchange('/command',body)
    def result(self,rid): return self.exchange('/request/'+urllib.parse.quote(rid,safe=':'))
    def waveform(self,after=None,stream=None):
        query={'limit':512}
        if after is not None: query['after']=after
        if stream is not None: query['stream']=stream
        return self.exchange('/waveform?'+urllib.parse.urlencode(query))

    def gowin(self,after=None,stream=None):
        query={'limit':512}
        if after is not None: query['after']=after
        if stream is not None: query['stream']=stream
        return self.exchange('/gowin?'+urllib.parse.urlencode(query))


def empty_state():
    return dict(source='host-bridge',firmware=None,host_online=False,connected=False,synced=False,
                status_fresh=False,telemetry_fresh=False,received_at=0,angle_received_at=0,arm_received_at=0,
                fpga_time_ms=None,arm_fpga_time_ms=None,mode=None,mode_name='WAITING',fault=None,
                parameters={},parameter_specs=[],requests={},status={},gesture=None,gesture_event=None,gesture_runtime=None,
                precision=None,arm_relative_deg=None,target_relative_deg=None,adaptive=None,adaptive_gains=None,
                fd2=None,capabilities={},replay={},transport={},display_fresh=False,motion={},swing={},
                arm_reference_deg=None,final_target_deg=None,action_reason=None,
                **{name:None for name in OBSERVATIONS})


class TelemetryAdapter:
    def __init__(self):
        self.precise=PreciseTelemetryAdapter()
        self.fd2=fd2_telemetry.FD2TelemetryAdapter()
        self.family=None
        self.source=None
        self.epoch=0
        self.pages={}
        self.stamps={}
        self.arrivals={}
        self.awaiting_epoch=set()
        self.was_live=False

    def clear(self):
        self.precise.clear()
        self.fd2.clear()
        self.epoch+=1
        self.pages.clear(); self.stamps.clear(); self.arrivals.clear()
        self.awaiting_epoch.clear()

    def invalidate(self):
        # A transport/status gap invalidates display evidence, not deduplication.
        # The same Host may still expose cached pages when it becomes reachable.
        self.epoch+=1
        self.pages.clear(); self.arrivals.clear(); self.was_live=False
        self.precise.invalidate()
        self.fd2.invalidate()

    def observe(self,snapshot,now,expected='auto',waveform=None):
        variant=identify(snapshot,expected)
        family='FD2' if snapshot.get('protocol') == 'FD2' else 'legacy'
        if self.family != family:
            self.clear(); self.source=None; self.family=family
        if family == 'FD2':
            return self.fd2.observe(snapshot,now,waveform,empty_state())
        status=snapshot.get('status') if isinstance(snapshot.get('status'),dict) else {}
        age=snapshot.get('status_age_s')
        live=bool(snapshot.get('connected') and snapshot.get('synced') and status and number(age) and 0<=age<=1)
        key=(variant,snapshot.get('log_directory'))
        source_changed=key!=self.source
        reset=source_changed or (self.was_live and not live)
        telemetry=snapshot.get('telemetry') or {}
        if not isinstance(telemetry,dict): telemetry={}
        clock_reset=live and any(isinstance(telemetry.get(page),dict) and integer(telemetry[page].get('timestamp_ms'),0,0xffffffff) is not None and
                        (telemetry[page]['timestamp_ms'] < self.stamps[page])
                        for page in ('angle','arm') if page in self.stamps and page not in self.awaiting_epoch)
        if source_changed:
            self.clear(); reset=True
        elif clock_reset:
            old_stamps=dict(self.stamps)
            self.clear(); reset=True
            for page,stamp in old_stamps.items():
                current=telemetry.get(page)
                current_stamp=integer(current.get('timestamp_ms'),0,0xffffffff) if isinstance(current,dict) else None
                if current_stamp is None or current_stamp>=stamp:
                    # This page still belongs to the previous device epoch.
                    self.stamps[page]=stamp if current_stamp is None else current_stamp
                    self.awaiting_epoch.add(page)
        elif reset:
            self.invalidate()
        self.source=key; self.was_live=live
        data=empty_state()
        data.update(firmware=variant,host_online=True,connected=bool(snapshot.get('connected')),synced=bool(snapshot.get('synced')),
                    status_fresh=live,status_age_s=age if number(age) else None,status=status,
                    note=snapshot.get('note',''),session=snapshot.get('log_directory'),
                    requests={k:{a:b for a,b in v.items() if a!='fingerprint'} for k,v in (snapshot.get('requests') or {}).items() if isinstance(v,dict)},
                    parameter_specs=[dict(spec) for spec in snapshot.get('parameter_specs',[]) if isinstance(spec,dict) and spec.get('name') in PARAMETER_BOUNDS])
        revision=status.get('revision')
        data['parameters']={name:dict(value=row.get('value'),revision=row.get('revision'),current=bool(live and row.get('revision')==revision))
                            for name,row in (snapshot.get('parameters') or {}).items() if isinstance(row,dict)}
        if variant=='SP24':
            return self.precise.observe(snapshot,data,now,live,waveform,reset)
        rows=[]
        if live:
            mode=integer(status.get('mode'),0,4)
            data.update(mode=mode,mode_name='SWING' if status.get('swing_active') else {0:'IDLE',1:'ZERO',2:'ARMED',3:'BALANCE',4:'FAULT'}.get(mode,'UNKNOWN'),
                        fault=integer(status.get('fault'),0,255),revision=revision)
            for page in ('angle','arm'):
                source=telemetry.get(page)
                if not isinstance(source,dict): continue
                stamp=integer(source.get('timestamp_ms'),0,0xffffffff)
                if stamp is None or self.stamps.get(page)==stamp: continue
                if page in self.awaiting_epoch and stamp>=self.stamps[page]: continue
                self.awaiting_epoch.discard(page)
                self.stamps[page]=stamp; self.pages[page]=dict(source); self.arrivals[page]=now
                row=dict(sample_id=f'{self.epoch}:{page}:{stamp}',received_at=now,fpga_time_ms=stamp)
                if page=='angle':
                    row.update(channels=['angle_deg','angle_velocity_deg_s'],angle_deg=self.q20(source.get('alpha_q20')),
                               angle_velocity_deg_s=self.q20(source.get('alpha_dot_q20')))
                else:
                    pwm=integer(source.get('pwm'),-32768,32767)
                    row.update(channels=['duty_actual_percent'],duty_actual_percent=None if pwm is None else pwm*100/32768)
                rows.append(row)
            for page in ('angle','arm'):
                arrival=self.arrivals.get(page,0)
                if page not in self.pages or now-arrival>2: continue
                source=self.pages[page]
                if page=='angle':
                    data.update(angle_deg=self.q20(source.get('alpha_q20')),angle_velocity_deg_s=self.q20(source.get('alpha_dot_q20')),
                                arm_velocity_deg_s=self.q20(source.get('theta_dot_q20')),angle_received_at=arrival,fpga_time_ms=source['timestamp_ms'])
                else:
                    pwm=integer(source.get('pwm'),-32768,32767)
                    data.update(encoder=integer(source.get('encoder_count'),-2**31,2**31-1),
                                duty_actual_percent=None if pwm is None else pwm*100/32768,
                                arm_received_at=arrival,arm_fpga_time_ms=source['timestamp_ms'])
            data['received_at']=max(data['angle_received_at'],data['arm_received_at'])
            data['telemetry_fresh']=bool(data['received_at'])
            data['gesture']=self.gesture(telemetry.get('gesture'))
            if variant=='SA21':
                data['adaptive']=telemetry.get('adaptive')
                data['adaptive_gains']=telemetry.get('adaptive_gains')
        return data,rows,reset

    @staticmethod
    def q20(value):
        value=integer(value,-2**31,2**31-1)
        return None if value is None else value*180/math.pi/1048576

    @staticmethod
    def gesture(row):
        if not isinstance(row,dict): return None
        try:
            sensor=bytes.fromhex(row['sensor_status']); motion=bytes.fromhex(row['motion_status'])
            if len(sensor)!=8 or len(motion)!=8: return None
            return dict(code=sensor[5]>>3,error=sensor[4],error_count=int.from_bytes(sensor[6:8],'little'),
                        sensor_id=f'{sensor[1]:02X}{sensor[0]:02X}',active=bool(motion[0]&1),busy=bool(motion[0]&2),
                        accepted=bool(motion[0]&4),rejected=bool(motion[0]&8),sequence=int.from_bytes(motion[4:8],'little'))
        except (ValueError,KeyError,TypeError): return None
