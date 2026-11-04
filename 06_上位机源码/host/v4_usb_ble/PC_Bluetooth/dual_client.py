"""One command owner/session with USB primary and an optional BLE key route.

Importing starts no thread, opens no port and imports no device driver.
"""
from collections import Counter
from contextlib import nullcontext
import queue
import threading
import time

from client import BLEOwner, BLEConnection, USBConnection, STALE_RX_ERROR
from protocol import *


class DualOwner(BLEOwner):
    def __init__(self,address,log_directory,*,transport='usb',port=None,baudrate=230400,
                 usb_factory=USBConnection,ble_factory=BLEConnection,
                 logger_factory=None,lease_factory=None):
        if not isinstance(baudrate,int) or isinstance(baudrate,bool) or not 300<=baudrate<=4000000:
            raise ValueError('USB波特率需要是300～4000000之间的整数')
        options={'transport':transport,'lease_factory':lease_factory}
        if logger_factory is not None:options['logger_factory']=logger_factory
        endpoint=(port or address) if transport=='usb' else address
        super().__init__(endpoint,log_directory,**options)
        self.primary=transport;self.usb_port=endpoint if transport=='usb' else None;self.usb_baudrate=baudrate
        self.ble_address=address if not str(address).upper().startswith('COM') else 'D0:54:7B:AA:0C:84'
        self.factories={'usb':usb_factory,'ble':ble_factory}
        self.links={};self.pending_routes={};self.pending_mirrors={}
        self.route_metadata={};self.route_counts={'usb':Counter(sequence_gaps=0),'ble':Counter(sequence_gaps=0)}
        self.control_queue=queue.Queue(maxsize=32);self.attach_thread=None;self.close_threads=[]
        self.attach_generation=0;self.ble_connecting=False;self.ble_notice=''
        self.secondary_quarantined=False
        self.link_errors={'usb':'','ble':''};self.duplicate_frames=0;self.reordered_frames=0
        self.state.last_ble_ack=None;self.state.last_usb_ack=None
        self.state.retired_actions=[]
        self.state.usb_baudrate=baudrate;self.state.sequence_gap_scope='merged snapshot gaps; use routes[name].sequence_gaps for physical route gaps'
        self._publish_routes()

    def _new_link(self,name,connection):
        parser=FrameParser(on_reject=lambda raw,reason,crc_ok:self._route_rejected(name,raw,reason,crc_ok),
                           on_observe=lambda raw,fw,schema:self._route_observed(name,raw,fw,schema))
        metadata=getattr(connection,'metadata',getattr(getattr(connection,'port',None),'metadata',{}))
        return dict(connection=connection,parser=parser,connected=True,ready=False,
                    quarantined=False,
                    last_rx=0.,last_ack_rx=0.,last_b=None,last_d=None,
                    heartbeat_due=0.,deadline=time.monotonic()+3.,metadata=dict(metadata),
                    observed_firmware=None,observed_schema=None)

    def _make_connection(self,name,address):
        options=dict(io_sink=lambda route,direction,raw:self._route_io(name,route,direction,raw),
            event_sink=lambda event,**data:self._route_event(name,event,**data))
        if name=='usb':options['baudrate']=self.usb_baudrate
        return self.factories[name](address,**options)

    def _diagnostic_state(self):
        result=super()._diagnostic_state();state=self.state.snapshot()
        result.update(routes=state.get('routes'),primary=self.primary,usb_baudrate=self.usb_baudrate,
            duplicate_frames=self.duplicate_frames,reordered_frames=self.reordered_frames,
            sequence_gap_scope=state.get('sequence_gap_scope'))
        return result

    def _route_io(self,name,route,direction,raw):
        self._physical_io(route,direction,raw)
        with self.state.lock:
            self.route_counts[name]['rx_bytes' if direction==0 else 'tx_bytes']+=len(raw)

    def _route_event(self,name,event,**data):
        self.diagnostics.event(event,link=name,**data)
        if event=='physical_write' and name=='ble' and 'request_id' in data and data.get('offset',0)+data.get('bytes',0)==48:
            self.physical_sent_times[(data['request_id'],name)]=time.monotonic()
        if event=='connection_tuning_report':
            with self.state.lock:self.route_metadata[name]=dict(data)

    def _route_observed(self,name,raw,fw,schema):
        link=self.links.get(name)
        if link is not None:
            link['observed_firmware']=fw
            if schema is not None:link['observed_schema']=schema
        if name==self.primary:self._observed(raw,fw,schema)

    def _route_rejected(self,name,raw,reason,crc_ok):
        self._rejected(raw,f'{name}: {reason}',crc_ok)

    def _publish_routes(self):
        routes={}
        for name in ('usb','ble'):
            link=self.links.get(name)
            with self.state.lock:counts=dict(self.route_counts[name]);metadata=self.route_metadata.get(name,link['metadata'] if link else {})
            routes[name]=dict(connected=bool(link and link['connected']),ready=bool(link and link['ready']),
                quarantined=bool(name=='ble' and self.secondary_quarantined or link and link.get('quarantined')),
                connecting=name=='ble' and self.ble_connecting,error=self.link_errors[name],
                last_rx=link['last_rx'] if link else 0.,last_ack_rx=link['last_ack_rx'] if link else 0.,
                observed_firmware=link['observed_firmware'] if link else None,
                observed_schema=link['observed_schema'] if link else None,
                crc_errors=link['parser'].crc_errors if link else 0,
                format_errors=link['parser'].format_errors if link else 0,
                discarded_bytes=link['parser'].discarded_bytes if link else 0,
                metadata=metadata,**counts)
        position='ble' if routes['ble']['ready'] else 'usb' if routes['usb']['ready'] else None
        with self.state.lock:
            self.state.routes=routes;self.state.usb_connected=routes['usb']['connected'];self.state.usb_ready=routes['usb']['ready']
            self.state.ble_connected=routes['ble']['connected'];self.state.ble_ready=routes['ble']['ready']
            self.state.ble_connecting=self.ble_connecting;self.state.ble_error=self.link_errors['ble'];self.state.ble_notice=self.ble_notice
            self.state.position_route=position;self.state.duplicate_frames=self.duplicate_frames;self.state.reordered_frames=self.reordered_frames
            primary=self.links.get(self.primary)
            self.state.connection_metadata=routes[self.primary]['metadata']
            self.state.connected=bool(primary and primary['connected'])

    def attach_ble(self,address=None):
        if self.primary!='usb':raise RuntimeError('当前已是蓝牙主连接；加入USB需先停止并整体断开')
        if self.closed.is_set() or self.stop_event.is_set() or not self.thread or not self.state.connected:raise RuntimeError('请先连接USB主路')
        with self.command_lock:
            if self.ble_connecting or 'ble' in self.links or (self.attach_thread and self.attach_thread.is_alive()):raise RuntimeError('蓝牙已连接、正在连接或仍在关闭旧初始化')
            self.attach_generation+=1;generation=self.attach_generation
            self.ble_address=address or self.ble_address;self.ble_connecting=True;self.link_errors['ble']=''
        self._publish_routes()
        def create():
            connection=None
            try:
                connection=self._make_connection('ble',self.ble_address)
                if self.stop_event.is_set() or generation!=self.attach_generation:
                    connection.close();connection=None
                self.control_queue.put(('attach',generation,connection,''))
            except Exception as exc:self.control_queue.put(('attach',generation,None,str(exc)))
        self.attach_thread=threading.Thread(target=create,name='V4_BLE_Attach',daemon=True)
        self.attach_thread.start()

    def detach_ble(self):
        if self.primary=='ble':self.stop(wait=False);return
        with self.command_lock:self.attach_generation+=1
        self.control_queue.put_nowait(('detach',0,None,''))

    def attach_usb(self,port):
        raise RuntimeError('加入USB需先停止并整体断开，再以USB主路连接；不会在运行中换会话')

    def detach_usb(self):
        if self.primary=='usb':self.stop(wait=False)

    def _is_secondary_position(self,rid,entry):
        return (self.primary=='usb' and entry[0] in (POSITION,CANCEL_HOLD)
                and self.pending_routes.get(rid)=={'ble'})

    def _quarantine_secondary_position(self,rid,reason):
        """Retire an unconfirmed BLE action, without retrying or locking USB.

        A later explicit user command receives a new globally increasing event.
        The FPGA router rejects an older event after that new event is accepted.
        This does not infer whether the older action has already executed.
        """
        entry=self.pending.get(rid)
        if not entry or not self._is_secondary_position(rid,entry):return False
        self.pending.pop(rid,None);self.pending_routes.pop(rid,None)
        for key in [key for key in self.physical_sent_times if isinstance(key,tuple) and key[0]==rid]:
            self.physical_sent_times.pop(key,None)
        link=self.links.get('ble')
        self.secondary_quarantined=True
        if link:link['ready']=False;link['quarantined']=True
        self.link_errors['ble']='位置命令确认丢失；旧命令结果未知，后续新按键使用USB'
        self.ble_notice='蓝牙位置确认异常；旧命令不重发，后续新按的位置键走USB。蓝牙可断开后重连'
        retired=dict(request_id=rid,opcode=entry[0],route='ble',
                     reason=reason,outcome='unknown',replayed=False)
        with self.state.lock:self.state.retired_actions=(self.state.retired_actions+[retired])[-32:]
        # Commands queued before the failure keep their original route. Drop
        # those unsent BLE positions; never turn them into silent USB retries.
        with self.command_lock:
            retained=[];dropped=0
            while True:
                try:queued=self.queue.get_nowait()
                except queue.Empty:break
                if queued[5]=='ble' and queued[2] in (POSITION,CANCEL_HOLD):dropped+=1
                else:retained.append(queued)
            for queued in retained:self.queue.put_nowait(queued)
        self.diagnostics.event('secondary_position_unconfirmed',**retired,dropped_unsent_ble_positions=dropped)
        self._publish_routes()
        return True

    def _close_secondary(self,reason='蓝牙已断开'):
        for rid,entry in list(self.pending.items()):
            self._quarantine_secondary_position(rid,'secondary_disconnected_without_ack')
        link=self.links.pop('ble',None);self.link_errors['ble']=reason;self.ble_connecting=False
        if link:
            for rid,names in list(self.pending_routes.items()):
                if 'ble' in names:
                    entry=self.pending.get(rid)
                    if entry and entry[0] not in SAFE:
                        with self.state.lock:self.state.error='蓝牙动作结果未知，不会转USB重发；请先停止确认'
            worker=threading.Thread(target=link['connection'].close,name='V4_BLE_Close',daemon=True)
            worker.start();self.close_threads.append(worker)

    def _drain_controls(self):
        while True:
            try:kind,generation,connection,error=self.control_queue.get_nowait()
            except queue.Empty:break
            if kind=='detach':self._close_secondary()
            elif generation!=self.attach_generation or self.stop_event.is_set():
                if connection:
                    worker=threading.Thread(target=connection.close,daemon=True);worker.start();self.close_threads.append(worker)
                self.ble_connecting=False
            else:
                self.ble_connecting=False
                if error:self.link_errors['ble']='蓝牙连接失败：'+error
                elif connection:
                    self.secondary_quarantined=False
                    self.links['ble']=self._new_link('ble',connection)
                    self.ble_notice='蓝牙已连接，正在用同一会话确认副路；不会发送第二个HELLO'

    def _choose_route(self,opcode):
        if opcode in (STOP,ESTOP):
            if 'usb' in self.links and self.links['usb']['connected']:return 'usb'
            if 'ble' in self.links and self.links['ble']['connected']:return 'ble'
            raise RuntimeError('没有可用的停止通道')
        if opcode in (POSITION,CANCEL_HOLD) and self.primary=='usb':
            if self.links.get('ble',{}).get('ready'):return 'ble'
            self.ble_notice='蓝牙位置通道未就绪，本次明确使用USB位置命令'
        return self.primary

    def command(self,opcode,group=0,payload=b''):
        name=self._choose_route(opcode)
        request(opcode,1,group,payload,session=self.session,event=1,route=0 if name=='usb' else 1)
        with self.state.lock:
            if not self.state.connected:raise RuntimeError('主连接尚未建立')
            if not self.state.authenticated and opcode not in (STATUS,STOP,ESTOP):raise RuntimeError('尚未收到主路正常命令回包')
            if (self.state.error or self.state.log_error) and opcode not in SAFE:raise RuntimeError('当前错误已锁定控制：'+(self.state.error or self.state.log_error))
        if opcode in (SET_GAINS,ZERO,RUN):self._require_stopped(parameters=opcode==SET_GAINS)
        if opcode==RUN:self._require_zero()
        if opcode in (POSITION,CANCEL_HOLD):self._require_balance()
        with self.command_lock:
            self.counter+=1
            if opcode in (STOP,ESTOP):
                retained=[]
                while True:
                    try:entry=self.queue.get_nowait()
                    except queue.Empty:break
                    if entry[2] in SAFE:retained.append(entry)
                for entry in retained:self.queue.put_nowait(entry)
            self.queue.put_nowait((0 if opcode in (STOP,ESTOP) else 1,self.counter,opcode,group,bytes(payload),name))
        self._publish_routes()

    def _send(self,opcode,group=0,payload=b'',route=None):
        name=route or self._choose_route(opcode)
        link=self.links.get(name)
        if not link or not link['connected']:raise RuntimeError('所选通道已断开，请重新明确提交；不会自动换路重发')
        if opcode in (SET_GAINS,ZERO,RUN):self._require_stopped(parameters=opcode==SET_GAINS)
        if opcode==RUN:self._require_zero()
        if opcode in (POSITION,CANCEL_HOLD):
            self._require_balance()
            if not link['ready']:raise RuntimeError('位置通道未就绪，请重新明确提交；不会自动换路重发')
        if self.next_id>0xffffffff:raise RuntimeError('请求ID已耗尽，禁止回绕或自动重连')
        rid=self.next_id;self.next_id+=1
        if opcode in (STOP,ESTOP):
            for old_id,entry in list(self.pending.items()):
                if entry[0] not in SAFE:
                    self.pending.pop(old_id,None);self.pending_routes.pop(old_id,None)
                    retired=dict(request_id=old_id,opcode=entry[0],reason='retired_by_explicit_stop_no_replay',outcome_before_stop='not_inferred')
                    with self.state.lock:self.state.retired_actions=(self.state.retired_actions+[retired])[-32:]
                    self.diagnostics.event('action_retired_by_stop',**retired)
                    for key in [key for key in self.physical_sent_times if isinstance(key,tuple) and key[0]==old_id]:self.physical_sent_times.pop(key,None)
        raw=request(opcode,rid,group,payload,session=self.session,event=rid,route=0 if name=='usb' else 1)
        self.pending[rid]=(opcode,group,bytes(payload),time.monotonic());self.pending_routes[rid]={name}
        write_error=None
        try:
            count=link['connection'].write(raw)
            if count is not None and count!=48:raise OSError('请求写入不完整，结果未知且不会重发')
            if not getattr(link['connection'],'async_write',False):self.physical_sent_times[(rid,name)]=time.monotonic()
        except Exception as exc:
            if opcode not in SAFE:
                if not self._quarantine_secondary_position(rid,'secondary_write_outcome_unknown'):
                    with self.state.lock:self.state.error=f'{name}动作写入结果未知，不会自动重发或换路重发'
            if opcode not in (STOP,ESTOP):raise
            write_error=exc
        with self.state.lock:
            self.state.sent+=1
            if opcode not in SAFE:self.state.sent_mutating_commands+=1
        # Mirror only idempotent STOP/ESTOP, using the same logical request id.
        # The router intentionally handles each STOP, not exactly-once dedup.
        mirrored=False
        if opcode in (STOP,ESTOP) and name=='usb' and self.links.get('ble',{}).get('connected'):
            try:
                mirror=request(opcode,rid,group,payload,session=self.session,event=rid,route=1)
                self.pending_routes[rid].add('ble')
                count=self.links['ble']['connection'].write(mirror)
                if count is not None and count!=48:raise OSError('蓝牙停止镜像写入不完整')
                mirrored=True
                self.diagnostics.event('stop_mirrored',request_id=rid,event_id=rid,usb_first=True)
            except Exception as exc:
                self.pending_routes[rid].discard('ble');self.link_errors['ble']='停止镜像失败：'+str(exc)
        if write_error is not None:
            self.diagnostics.event('stop_primary_write_error',request_id=rid,reason=repr(write_error),ble_mirror_queued=mirrored)
            if not mirrored:raise write_error
            with self.state.lock:self.state.message='USB停止写入结果未知，已向蓝牙提交同一停止事件；等待回包确认'
        return rid

    def _receive_route(self,name,raw,item):
        link=self.links.get(name)
        if not link:return
        now=time.monotonic();link['last_rx']=now;self.route_counts[name]['received']+=1
        if item['kind']=='ack':
            rid=item['request_id'];entry=self.pending.get(rid)
            if not entry or name not in self.pending_routes.get(rid,set()) or entry[0]!=item['opcode']:
                self.route_counts[name]['unmatched_ack']+=1;return
            if item['status']==0 and item['opcode'] in READS:
                link['last_ack_rx']=now
                if not link.get('quarantined'):
                    link['ready']=True;self.link_errors[name]=''
                    if name=='ble' and self.primary=='usb':self.ble_notice='USB负责常规控制与遥测，蓝牙位置通道已就绪'
            elif item['opcode'] in READS and item['status']!=0:
                link['ready']=False;self.link_errors[name]=f'板端拒绝当前会话回包，状态{item["status"]}'
                if name==self.primary:
                    with self.state.lock:self.identity_failed=True;self.state.authenticated=False;self.state.error=self.link_errors[name]+'；请停止后重新连接'
            with self.state.lock:
                self.state.last_usb_ack=item if name=='usb' else self.state.last_usb_ack
                self.state.last_ble_ack=item if name=='ble' else self.state.last_ble_ack
                prior_ack,prior_rx=self.state.last_ack,self.state.last_ack_rx
            self.pending_routes.pop(rid,None)
            for key in [key for key in self.physical_sent_times if isinstance(key,tuple) and key[0]==rid]:self.physical_sent_times.pop(key,None)
            super()._receive(raw,item)
            if name!=self.primary:
                with self.state.lock:self.state.last_ack,self.state.last_ack_rx=prior_ack,prior_rx
            return
        if not link['ready']:
            self.route_counts[name]['unbound_frames']+=1;return
        if item['kind']=='diagnostic' and item['session']!=self.session:
            self.route_counts[name]['wrong_session_d']+=1;return
        key='last_b' if item['kind']=='compact' else 'last_d'
        old_route=link[key];link[key]=item
        if old_route and item['board_ms']<old_route['board_ms'] and old_route['board_ms']-item['board_ms']<0x80000000:
            link['ready']=False;self.link_errors[name]='板端时间回退，请停止后重新连接'
            if name==self.primary:
                with self.state.lock:self.identity_failed=True;self.state.authenticated=False;self.state.error=self.link_errors[name]
            return
        if item['kind']=='compact' and old_route:
            route_distance=(item['sequence']-old_route['sequence'])&0xffffffff
            if 1<route_distance<0x80000000:self.route_counts[name]['sequence_gaps']+=route_distance-1
        old=self.state.latest if item['kind']=='compact' else self.state.diagnostic
        if old:
            if item['kind']=='compact':
                distance=(item['sequence']-old['sequence'])&0xffffffff
                if distance==0:self.duplicate_frames+=1;return
                if distance>=0x80000000:self.reordered_frames+=1;return
            else:
                forward=(item['board_ms']-old['board_ms'])&0xffffffff
                if forward==0:self.duplicate_frames+=1;return
                if forward>=0x80000000:self.reordered_frames+=1;return
        super()._receive(raw,item)

    def _poll_links(self):
        received=False
        for name in ('usb','ble'):
            link=self.links.get(name)
            if not link:continue
            try:data=link['connection'].read(4096)
            except Exception as exc:
                if name==self.primary:raise
                self._close_secondary('蓝牙已断开：'+str(exc));continue
            if data:received=True
            for raw,item in link['parser'].feed(data):self._receive_route(name,raw,item)
        return received

    def _health(self,now):
        primary=self.links[self.primary]
        if now>primary['deadline'] and not primary['ready']:
            with self.state.lock:self.identity_failed=True;self.state.authenticated=False;self.state.error='主路3秒内未收到匹配的正常命令回包'
        if primary['last_rx'] and now-primary['last_rx']>.5:
            with self.state.lock:self.state.authenticated=False;self.state.error=STALE_RX_ERROR
        secondary=self.links.get('ble') if self.primary=='usb' else None
        if secondary and not secondary.get('quarantined') and ((secondary['last_rx'] and now-secondary['last_rx']>.5) or (now>secondary['deadline'] and not secondary['ready'])):
            secondary['ready']=False;self.link_errors['ble']='蓝牙副路未收到新鲜正常回包，USB主路继续可用'
        with self.state.lock:
            self.state.crc_errors=sum(link['parser'].crc_errors for link in self.links.values())
            self.state.format_errors=sum(link['parser'].format_errors for link in self.links.values())
            self.state.parser_rejection_counts={name+':'+reason:count for name,link in self.links.items() for reason,count in link['parser'].rejection_counts.items()}
            self.state.diagnostic_capture_error=self.diagnostics.error;self.state.diagnostic_archive=self.diagnostics.archive_path
            self.state.log_status=f'已保存{self.logger.written}帧' if self.logger else '未开始'
            if self.state.error:self.state.identity_failure_reason=self.state.error
            self.state.diagnostic_warning='未收到长诊断帧；使用正常短回包操作' if not self.identity_d else ('详细诊断已超过1.5秒未更新' if now-self.state.diagnostic_rx>1.5 else '')
        for rid,entry in list(self.pending.items()):
            times=[self.physical_sent_times.get((rid,name)) for name in self.pending_routes.get(rid,set())]
            sent=min((value for value in times if value is not None),default=None)
            if sent is not None and now-sent>1:
                isolated=self._quarantine_secondary_position(rid,'secondary_ack_timeout')
                self.pending.pop(rid,None);self.pending_routes.pop(rid,None)
                for key in [key for key in self.physical_sent_times if isinstance(key,tuple) and key[0]==rid]:self.physical_sent_times.pop(key,None)
                with self.state.lock:
                    self.state.timeouts+=1
                    if entry[0] not in READS and not isolated:self.state.error='命令结果未知，不会自动重发或换路重发；请独立确认'
        self._publish_routes()

    def _run(self):
        if self.lease_factory is None:
            from hardware_mutex import SharedMutex
            lease=SharedMutex()
        else:lease=self.lease_factory()
        try:
            with lease:
                try:
                    self.logger=self.logger_factory(self.log_directory,4);self.logger.start()
                    connection=self._make_connection(self.primary,self.usb_port if self.primary=='usb' else self.ble_address)
                    self.links[self.primary]=self._new_link(self.primary,connection);self.port=connection
                    self._publish_routes();self._send(HELLO,route=self.primary)
                    while not self.stop_event.is_set():
                        now=time.monotonic();self._drain_controls()
                        for name,link in list(self.links.items()):
                            if now>=link['heartbeat_due'] and (name==self.primary or self.state.authenticated):
                                try:self._send(STATUS,route=name);link['heartbeat_due']=now+(.1 if name==self.primary else .5)
                                except Exception as exc:
                                    if name==self.primary:raise
                                    self._close_secondary('蓝牙写入失败：'+str(exc))
                        if self.logger.error:
                            with self.state.lock:self.state.log_error=self.logger.error
                            if not self.log_stop_sent:self._send(STOP);self.log_stop_sent=True
                        for _ in range(4):
                            try:entry=self.queue.get_nowait()
                            except queue.Empty:break
                            _,_,opcode,group,payload,name=entry
                            if opcode not in SAFE and any(value[0] not in SAFE for value in self.pending.values()):
                                self.queue.put_nowait(entry);break
                            if opcode not in SAFE and (self.state.error or self.state.log_error or not self.state.authenticated):continue
                            try:self._send(opcode,group,payload,route=name)
                            except RuntimeError as exc:self._event('请求未发送：'+str(exc))
                            except Exception as exc:
                                if name==self.primary:raise
                                self._close_secondary('蓝牙动作写入异常：'+str(exc))
                        got=self._poll_links();self._health(now)
                        if self.state.error or self.state.diagnostic_warning:self.diagnostics.export_on_first_error(self._diagnostic_state())
                        if not got:time.sleep(.001)
                finally:
                    # Stop once over USB, with an idempotent BLE mirror if open.
                    try:
                        if self.links:self._send(STOP)
                    except Exception:pass
                    until=time.monotonic()+.2
                    while self.links and time.monotonic()<until:
                        try:self._poll_links()
                        except Exception:break
                        time.sleep(.005)
                    self.attach_generation+=1
                    if self.attach_thread:
                        # Retain the one shared lease until an outstanding BLE
                        # constructor has returned and closed its canceled link.
                        while self.attach_thread.is_alive():self.attach_thread.join(.2)
                    self._drain_controls()
                    for link in list(self.links.values()):
                        try:link['connection'].close()
                        except Exception:pass
                    self.links.clear()
                    for worker in self.close_threads:worker.join(timeout=5)
        except Exception as exc:
            with self.state.lock:self.state.error=str(exc);self.state.message='通信已停止：'+str(exc)
        finally:
            with self.state.lock:self.state.connected=False;self.state.authenticated=False
            self._publish_routes()
            if self.logger:
                self.logger.close()
                with self.state.lock:self.state.log_error=self.logger.error;self.state.log_status=str(self.logger.archive_path if not self.logger.error else self.logger.partial_path)
                self.diagnostics.close(self._diagnostic_state())
                with self.state.lock:self.state.diagnostic_archive=self.diagnostics.archive_path;self.state.diagnostic_capture_error=self.diagnostics.error
            self.closed.set()
