"""Read-only replay of delivered physical evidence; never opens a transport."""
import json
import threading
import time
import zipfile
import struct
from pathlib import Path
from host.g3_c1_bridge import G3Bridge, load_upstream
from host.fd2_bridge import Bridge, json_safe

class ReplayBridge(G3Bridge):
    def __init__(self, profile, path, *, speed=1.0):
        if profile != 'G3_C1':
            if profile == 'V4_R28':
                raise ValueError('V4 Raw.v2 仅含 RX；ACK 不含会话且缺少原发送请求，无法证明身份绑定')
            raise ValueError('不支持的回放版本')
        if speed <= 0: raise ValueError('回放速度必须为正')
        self.path=Path(path); self.speed=speed
        with zipfile.ZipFile(self.path) as archive:
            meta=json.loads(archive.read('session.json'))
            if meta.get('format')!='ForestFive.Physical.v2' or meta.get('variant')!=5 or meta.get('chunk_header')!='<QQBBII':
                raise ValueError('日志格式或版本不匹配')
            if 'physical_chunks.bin' not in archive.namelist(): raise ValueError('缺少物理字节')
        super().__init__()
        self.replay.update(active=True,finished=False,label=self.path.name,
                           partial=bool(meta.get('snapshot') or meta.get('complete_session') is False or meta.get('error') or meta.get('raw_received',0)!=meta.get('raw_written',0)),error='',crc_errors=0)
        self.modules=load_upstream()
        self.owner=self.modules['client'].SerialOwner(5,'',self.path.parent)
        self.state=self.owner.state
        self.cancel=threading.Event(); self.thread=None
    def status(self):
        result=super().status()
        result.update(connected=False,synced=False,replay=dict(self.replay))
        result['transport']=dict(usb_connected=False,ble_connected=False)
        return result
    def command(self,body):
        raise ValueError('离线回放禁止命令')
    def run(self):
        parsers={0:self.modules['protocol'].FrameParser(),1:self.modules['protocol'].FrameParser()}
        first=None; last=None; start=time.monotonic(); gap=False
        try:
            for wall,mono,route,direction,raw in self.modules['trace_log'].iter_chunks(self.path):
                if route not in parsers or direction not in (0,1): raise ValueError('非法物理通道或方向')
                if last is not None and mono<last: raise ValueError('时间戳逆序')
                last=mono
                if first is None: first=mono
                delay=(mono-first)/1e9/self.speed-(time.monotonic()-start)
                if delay>0 and self.cancel.wait(delay): return
                if self.cancel.is_set(): return
                if direction: continue
                parser=parsers[route]
                before=parser.crc_errors
                decoded=parser.feed(raw)
                if parser.crc_errors!=before: gap=True
                self.replay['crc_errors']=sum(p.crc_errors for p in parsers.values())
                if parser.format_errors: raise ValueError('帧格式错误')
                for frame,item in decoded:
                    if item['kind']!='compact' and (item['variant']!=5 or item['wire_version']!=5 or item['firmware_id']!=self.modules['protocol'].FIRMWARE[5] or (item['kind'] in ('telemetry','diagnostic') and item['schema']!=self.modules['protocol'].SCHEMAS[5])):
                        raise ValueError('固件身份不匹配')
                    if item['kind'] not in ('telemetry','diagnostic'): continue
                    self.owner._receive(frame,item)
                    if self.owner._identity_failed: raise ValueError('固件身份不匹配')
                    with self.owner.state.lock:
                        accepted=(self.owner.state.last_control is item or self.owner.state.latest_diagnostic is item)
                    if accepted:
                        cursor=self.cursor
                        self.capture(item,received_at=wall/1e9)
                        if gap and self.cursor!=cursor:
                            with self.lock: self.rows[-1]['gap_before']=True
                            gap=False
        except Exception as exc:
            self.replay['error']=str(exc)
            self.replay['partial']=True
        finally: self.replay['finished']=True
    def start(self):
        if self.thread is not None: raise RuntimeError('回放已经启动')
        self.thread=threading.Thread(target=self.run,name='DeliveryReplay',daemon=True); self.thread.start(); return self
    def stop(self):
        self.cancel.set()
        if self.thread: self.thread.join(timeout=2)
        if self.server: self.server.shutdown(); self.server.server_close(); self.server=None


class DS13ReplayBridge(Bridge):
    """Read DS13 frames.bin only; never instantiate or start a device owner."""
    def __init__(self, path, *, speed=1.0):
        from host.ds13_bridge import load_upstream as load_ds13
        if speed <= 0: raise ValueError('回放速度必须为正')
        super().__init__(5, replay=True)
        self.path=Path(path)
        if self.path.is_dir(): self.path=self.path/'frames.bin'
        if not self.path.is_file(): raise ValueError('未找到 DS13 frames.bin')
        self.speed=speed;self.protocol=load_ds13()['protocol']
        self.cancel=threading.Event();self.thread=None;self.latest=None
        self.replay.update(active=True,finished=False,label=self.path.name,partial=True,error='',frame_errors=0)

    def status(self):
        with self.lock:
            return json_safe(dict(protocol='FD2',variant=5,user_code='44533133',schema='44535002',wire_version=5,
                session=self.session,connected=False,synced=False,status_age_s=None,control_age_s=None,
                diagnostic_age_s=None,host=dict(latest=self.latest),requests={},
                transport=dict(usb_connected=False,ble_connected=False),replay=dict(self.replay)))

    def command(self,body):
        raise ValueError('离线回放禁止命令')

    def run(self):
        parser=self.protocol.FrameParser(); first_board=None; start=time.monotonic(); gap=False
        try:
            with self.path.open('rb') as source:
                while not self.cancel.is_set():
                    chunk=source.read(256)
                    if not chunk: break
                    before=parser.errors;frames=parser.feed(chunk)
                    if parser.errors!=before: gap=True
                    self.replay['frame_errors']=parser.errors
                    for raw,item in frames:
                        if item['kind'] not in ('telemetry','diagnostic'): continue
                        if not item.get('identity_ok'): raise ValueError('固件身份不匹配')
                        board=item['board_ms']
                        if first_board is None: first_board=board
                        elapsed=(board-first_board)&0xffffffff
                        if elapsed>=0x80000000: raise ValueError('板端时间逆序')
                        delay=elapsed/1000/self.speed-(time.monotonic()-start)
                        if delay>0 and self.cancel.wait(delay): return
                        frame=dict(item,wire_version=raw[4])
                        if item['kind']=='telemetry':
                            frame['quiet_q4']=struct.unpack_from('<H',raw,52)[0]
                            self.latest=frame
                        cursor=self.cursor;self.capture(frame)
                        if gap and self.cursor!=cursor:
                            with self.lock:self.rows[-1]['gap_before']=True
                            gap=False
            if parser.buffer:
                self.replay['partial']=True
        except Exception as exc:
            self.replay.update(error=str(exc),partial=True)
        finally:
            self.replay['finished']=True

    def start(self):
        if self.thread is not None: raise RuntimeError('回放已经启动')
        self.thread=threading.Thread(target=self.run,name='DS13Replay',daemon=True);self.thread.start();return self

    def stop(self):
        self.cancel.set()
        if self.thread:self.thread.join(timeout=2)
        if self.server:self.server.shutdown();self.server.server_close();self.server=None


class V4DiagnosticReplayBridge(Bridge):
    """Replay self-identifying R28 D frames; unbound compact B frames are omitted."""
    def __init__(self,path,*,speed=1.0,profile='V4_R28'):
        if profile == 'V4_R28':
            from host.v4_r28_bridge import load_upstream
            protocol = load_upstream()[1]
            firmware = '56345239'
        elif profile == 'V4_USB_BLE':
            from host.v4_usb_ble_bridge import load_upstream
            protocol = load_upstream()['protocol']
            firmware = '5634523A'
        else:
            raise ValueError('不支持的 V4 回放版本')
        if speed<=0: raise ValueError('回放速度必须为正')
        self.path=Path(path);self.speed=speed
        with zipfile.ZipFile(self.path) as archive:
            meta=json.loads(archive.read('session.json'))
            if (meta.get('format')!='ForestD1.Raw.v2' or meta.get('variant')!=4 or
                meta.get('firmware')!=f'0x{firmware}' or meta.get('schema')!='0x56345208' or
                meta.get('wire_version')!=4 or meta.get('diagnostic_layout')!=5 or
                'records.bin' not in archive.namelist()):
                raise ValueError('R28 日志身份或格式不匹配')
        super().__init__(4,replay=True)
        self.protocol=protocol;self.firmware=firmware;self.cancel=threading.Event();self.thread=None
        self.replay.update(active=True,finished=False,label=self.path.name,partial=True,error='',
                           unbound_compact=0,crc_errors=0,
                           warning='仅回放可独立识别的诊断帧；compact 帧缺少会话绑定证据')

    def status(self):
        with self.lock:
            return json_safe(dict(protocol='FD2',variant=4,user_code=self.firmware,schema='56345208',wire_version=4,
                session=self.session,connected=False,synced=False,status_age_s=None,control_age_s=None,
                diagnostic_age_s=None,host={},requests={},transport=dict(usb_connected=False,ble_connected=False),
                replay=dict(self.replay)))

    def command(self,body): raise ValueError('离线回放禁止命令')

    def run(self):
        header=struct.Struct('<QQH');first=None;last=None;start=time.monotonic();device_session=None;gap=False
        try:
            with zipfile.ZipFile(self.path) as archive,archive.open('records.bin') as source:
                while not self.cancel.is_set():
                    raw_header=source.read(header.size)
                    if not raw_header: break
                    if len(raw_header)!=header.size: raise ValueError('日志记录头不完整')
                    wall,mono,length=header.unpack(raw_header)
                    if length not in (48,80,256): raise ValueError('日志记录长度无效')
                    raw=source.read(length)
                    if len(raw)!=length: raise ValueError('日志记录不完整')
                    if last is not None and mono<last: raise ValueError('时间戳逆序')
                    last=mono
                    if first is None:first=mono
                    delay=(mono-first)/1e9/self.speed-(time.monotonic()-start)
                    if delay>0 and self.cancel.wait(delay): return
                    try:item=self.protocol.decode(raw)
                    except (ValueError,struct.error) as exc:
                        if self.protocol.crc16(raw[:-2])!=struct.unpack_from('<H',raw,len(raw)-2)[0]:
                            self.replay['crc_errors']+=1;gap=True;continue
                        raise ValueError('回放帧格式或固件身份不匹配') from exc
                    if item['kind']=='compact':self.replay['unbound_compact']+=1;continue
                    if item.get('firmware_id') != int(self.firmware,16):
                        raise ValueError('回放帧固件身份与日志不匹配')
                    if item['kind']!='diagnostic':continue
                    if device_session is None:device_session=item['session']
                    elif item['session']!=device_session:raise ValueError('日志中设备会话发生变化')
                    cursor=self.cursor;self.capture(item,received_at=wall/1e9)
                    if gap and self.cursor!=cursor:
                        with self.lock:self.rows[-1]['gap_before']=True
                        gap=False
        except Exception as exc:self.replay.update(error=str(exc),partial=True)
        finally:self.replay['finished']=True

    def start(self):
        if self.thread is not None:raise RuntimeError('回放已经启动')
        self.thread=threading.Thread(target=self.run,name='R28DiagnosticReplay',daemon=True);self.thread.start();return self

    def stop(self):
        self.cancel.set()
        if self.thread:self.thread.join(timeout=2)
        if self.server:self.server.shutdown();self.server.server_close();self.server=None
