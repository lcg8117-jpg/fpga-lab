"""One explicitly selected WHEELTEC BLE UART route; no reconnect or motion retry."""
import asyncio
import concurrent.futures
import copy
from pathlib import Path
import sys
import threading

from bt10_profile import SERVICE,NOTIFY,WRITE,WINRT_OPTIONS

def _link_snapshot(client):
    """Read negotiated values, never report a preferred request as effective."""
    report={'query_errors':{}}
    try:report['mtu_size']=int(client.mtu_size)
    except Exception as exc:report['query_errors']['mtu_size']=repr(exc)
    native=getattr(getattr(client,'_backend',None),'_requester',None)
    if native is None:
        report['query_errors']['native_device']='WinRT requester is unavailable'
        return report
    try:
        params=native.get_connection_parameters()
        report['parameters']={'connection_interval_units':int(params.connection_interval),
            'connection_interval_ms':int(params.connection_interval)*1.25,
            'connection_latency_events':int(params.connection_latency),
            'link_timeout_units':int(params.link_timeout),'link_timeout_ms':int(params.link_timeout)*10}
    except Exception as exc:report['query_errors']['parameters']=repr(exc)
    try:
        phy=native.get_connection_phy()
        report['phy']={direction:{name:bool(getattr(info,name)) for name in
            ('is_coded_phy','is_uncoded1_m_phy','is_uncoded2_m_phy')}
            for direction,info in (('receive',phy.receive_info),('transmit',phy.transmit_info))}
    except Exception as exc:report['query_errors']['phy']=repr(exc)
    return report

class _ConnectionTuning:
    """Hold a Windows11 throughput preference for exactly this BLE session."""
    def __init__(self,client,*,preferred_type=None):
        self.client=client;self.request=None
        self.report={'requested_policy':'ThroughputOptimized','request_state':'not_requested',
            'before':_link_snapshot(client),'effective_parameters_must_be_read_back':True}
        native=getattr(getattr(client,'_backend',None),'_requester',None)
        try:
            if native is None:raise AttributeError('WinRT requester is unavailable')
            if preferred_type is None:
                from winrt.windows.devices.bluetooth import BluetoothLEPreferredConnectionParameters
                preferred_type=BluetoothLEPreferredConnectionParameters
            self.request=native.request_preferred_connection_parameters(preferred_type.throughput_optimized)
            if self.request is None:raise RuntimeError('Windows returned no connection-parameter request')
            self.report['request_state']='requested'
        except (ImportError,AttributeError,NotImplementedError) as exc:
            self.report['request_state']='unsupported';self.report['request_error']=repr(exc)
        except OSError as exc:
            code=getattr(exc,'winerror',None)
            self.report['request_state']='unsupported' if code is not None and (code&0xffffffff) in (0x80004001,0x80004002,0x80070032) else 'request_failed'
            self.report['request_error']=repr(exc)
        except Exception as exc:
            self.report['request_state']='request_failed';self.report['request_error']=repr(exc)
        self.refresh('after_request')
    def refresh(self,label):
        self.report[label]=_link_snapshot(self.client)
        if self.request is not None:
            try:
                value=self.request.status
                self.report['request_status_code']=int(value)
                self.report['request_status_name']=getattr(value,'name',str(value))
            except Exception as exc:self.report['request_status_error']=repr(exc)
        return copy.deepcopy(self.report)
    def close(self):
        request,self.request=self.request,None
        if request is not None:
            try:request.close();self.report['request_closed']=True
            except Exception as exc:
                self.report['request_closed']=False;self.report['request_close_error']=repr(exc)

async def _default_adapter_report():
    """Read the Windows default radio; label it explicitly on multi-radio PCs."""
    report={'scope':'Windows default adapter; not proof of the selected physical radio'}
    try:
        from winrt.windows.devices.bluetooth import BluetoothAdapter
        adapter=await BluetoothAdapter.get_default_async()
        if adapter is None:report['status']='no_default_adapter';return report
        for name in ('device_id','bluetooth_address','is_low_energy_supported',
                     'is_central_role_supported','is_low_energy_uncoded2_m_phy_supported'):
            try:report[name]=getattr(adapter,name)
            except Exception as exc:report[name+'_error']=repr(exc)
        try:
            radio=await adapter.get_radio_async()
            report['radio']={'name':radio.name,'state':int(radio.state),'kind':int(radio.kind)}
        except Exception as exc:report['radio_error']=repr(exc)
        try:
            from winrt.windows.devices.enumeration import DeviceInformation
            wanted=('System.Devices.DeviceInstanceId','System.Devices.DriverVersion','System.Devices.DriverProvider')
            info=await DeviceInformation.create_from_id_async(adapter.device_id,wanted)
            report['name']=info.name
            report['properties']={str(key):str(value) for key,value in info.properties.items() if key in wanted and value is not None}
        except Exception as exc:report['device_information_error']=repr(exc)
        report['status']='queried'
    except Exception as exc:report['status']='query_failed';report['error']=repr(exc)
    return report

def api():
    sys.path.insert(0,str(Path(__file__).parent/'vendor_ble'))
    from bleak import BleakClient,BleakScanner
    return BleakClient,BleakScanner

def characteristics(services):
    service=services.get_service(SERVICE)
    if service is None:raise RuntimeError('Observed WHEELTEC FFE0 service absent')
    writable=[c for c in service.characteristics if set(c.properties)&{'write','write-without-response'}]
    notify=[c for c in service.characteristics if set(c.properties)&{'notify','indicate'}]
    if len(writable)!=1 or len(notify)!=1:
        raise RuntimeError('Ambiguous BLE UART characteristics; no application packet sent')
    if writable[0].uuid.lower()!=WRITE or notify[0].uuid.lower()!=NOTIFY:
        raise RuntimeError('BT10 profile UUID mismatch; no application packet sent')
    return writable[0],notify[0]

class BLEPort:
    def __init__(self,address,*,io_sink=None,event_sink=None):
        self.address=address;self.lock=threading.Lock();self.buffer=bytearray();self.ready=threading.Event()
        self.stop=threading.Event();self.closed=False;self.error=None;self.loop=None;self.client=None;self.writer=None
        self.io_sink=io_sink;self.event_sink=event_sink;self.write_lock=None;self.pending_writes=0;self.command_generation=0
        self.synchronizing=True;self.startup_bytes=0
        self.metadata={};self.thread=threading.Thread(target=self._run,name='WHEELTEC_BLE_Owner',daemon=False)
        self.thread.start()
        if not self.ready.wait(30):
            self.stop.set();raise RuntimeError('BLE initialization timed out; no motor request sent')
        if self.error:
            self.thread.join(3);raise RuntimeError(self.error)
    def _disconnected(self,unused):
        if not self.stop.is_set():self.error='BLE link disconnected; no automatic reconnect'
        self.stop.set()
    def _notification(self,unused,data):
        if self.io_sink:self.io_sink(1,0,bytes(data))
        with self.lock:
            if getattr(self,'synchronizing',False):
                self.startup_bytes+=len(data);return
            if len(self.buffer)+len(data)>65536:
                self.error='BLE receive buffer exceeded';self.stop.set();return
            self.buffer.extend(data)
    async def _startup_sync(self):
        # Preserve every physical notification, but old startup cache cannot
        # authenticate or seed control state. This is the proven BT10 window.
        if self.event_sink:self.event_sink('ble_startup_sync_begin',duration_ms=2000)
        await asyncio.sleep(2.0)
        self._check()
        with self.lock:self.synchronizing=False
        if self.event_sink:self.event_sink('ble_startup_sync_complete',raw_bytes=self.startup_bytes)
    async def _session(self):
        Client,Scanner=api()
        device=await Scanner.find_device_by_address(self.address,timeout=8)
        if device is None:raise RuntimeError('The selected WHEELTEC is not advertising')
        async with Client(device,timeout=15,disconnected_callback=self._disconnected,
                          winrt=dict(WINRT_OPTIONS)) as client:
            self.client=client
            self.write_lock=asyncio.Lock()
            writer,notify=characteristics(client.services);self.writer=writer
            self.response='write-without-response' not in writer.properties
            self.metadata={'address':device.address,'name':device.name,'service':SERVICE,
                'write_uuid':writer.uuid,'notify_uuid':notify.uuid,'write_response':self.response,
                'characteristics':[{'uuid':c.uuid,'properties':c.properties} for c in client.services.get_service(SERVICE).characteristics]}
            tuning=_ConnectionTuning(client)
            async def bounded_adapter_query():
                return await asyncio.wait_for(_default_adapter_report(),timeout=1.8)
            adapter_task=asyncio.create_task(bounded_adapter_query())
            try:
                await client.start_notify(notify,self._notification)
                await self._startup_sync()
                try:
                    if not adapter_task.done():raise TimeoutError('Default adapter query did not complete inside startup window')
                    self.metadata['default_adapter']=adapter_task.result()
                except Exception as exc:self.metadata['default_adapter']={'status':'query_failed','error':repr(exc)}
                self.metadata['connection_tuning']=tuning.refresh('after_startup_sync')
                self._tuning_event('connected')
                self.ready.set()
                while not self.stop.is_set():await asyncio.sleep(.01)
                await client.stop_notify(notify)
            finally:
                if not adapter_task.done():adapter_task.cancel()
                await asyncio.gather(adapter_task,return_exceptions=True)
                tuning.refresh('before_disconnect');tuning.close()
                self.metadata['connection_tuning']=copy.deepcopy(tuning.report)
                self._tuning_event('disconnecting')
    def _tuning_event(self,stage):
        if self.event_sink:
            try:self.event_sink('connection_tuning_report',stage=stage,connection_tuning=copy.deepcopy(self.metadata.get('connection_tuning',{})),default_adapter=copy.deepcopy(self.metadata.get('default_adapter',{})))
            except Exception as exc:self.metadata['connection_tuning_event_error']=repr(exc)
    def _run(self):
        self.loop=asyncio.new_event_loop();asyncio.set_event_loop(self.loop)
        try:self.loop.run_until_complete(self._session())
        except BaseException as error:self.error=repr(error)
        finally:
            self.closed=True;self.ready.set();self.loop.run_until_complete(self.loop.shutdown_asyncgens());self.loop.close()
    def _check(self):
        if self.error:raise OSError(self.error)
        if self.closed or self.stop.is_set():raise OSError('BLE route closed')
    @property
    def in_waiting(self):
        self._check()
        with self.lock:return len(self.buffer)
    def read(self,n):
        self._check()
        with self.lock:
            data=bytes(self.buffer[:n]);del self.buffer[:n];return data
    async def _write(self,raw,generation=None):
        # Serialize every fragment of a frame. A timed-out fragment has an
        # unknown outcome and is never retried or followed by another frame.
        import struct
        rid=struct.unpack_from('<I',raw,8)[0];sent=0
        if generation is None:generation=getattr(self,'command_generation',0)
        try:
            async with self.write_lock:
                self._check()
                for offset in range(0,len(raw),20):
                    if raw[5] not in (0x10,0x14) and generation!=getattr(self,'command_generation',0):
                        if self.event_sink:self.event_sink('ble_cancelled_by_stop',route=1,request_id=rid,confirmed_bytes=sent)
                        return sent
                    chunk=raw[offset:offset+20]
                    await asyncio.wait_for(self.client.write_gatt_char(self.writer,chunk,response=self.response),timeout=.2)
                    sent+=len(chunk)
                    if self.io_sink:self.io_sink(1,1,chunk)
                    if self.event_sink:self.event_sink('physical_write',route=1,request_id=rid,offset=offset,bytes=len(chunk))
            return len(raw)
        except Exception as exc:
            self.error='BLE write outcome unknown; never retried: '+repr(exc);self.stop.set()
            if self.event_sink:self.event_sink('write_unknown',route=1,request_id=rid,confirmed_bytes=sent,reason=self.error)
            raise
        finally:
            with self.lock:self.pending_writes-=1
    def write(self,raw):
        self._check()
        if len(raw)!=48:raise ValueError('Only one complete 48-byte FD2 request may be queued')
        with self.lock:
            emergency=raw[5] in (0x10,0x14)
            if emergency:self.command_generation+=1
            if self.pending_writes>=(18 if emergency else 16):raise OSError('BLE bounded command queue full; request not queued')
            generation=self.command_generation
            self.pending_writes+=1
        task=asyncio.run_coroutine_threadsafe(self._write(bytes(raw),generation),self.loop)
        # The caller records this as queued, never as a completed physical TX.
        task.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)
        return len(raw)
    def close(self):
        self.stop.set()
        if self.thread is not threading.current_thread():self.thread.join(4)
        self.closed=True
