"""One explicitly selected WHEELTEC BLE UART route; no reconnect or motion retry."""
import asyncio
import concurrent.futures
from pathlib import Path
import sys
import threading

from bt10_profile import SERVICE,NOTIFY,WRITE,WINRT_OPTIONS

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
            await client.start_notify(notify,self._notification)
            await self._startup_sync()
            self.ready.set()
            while not self.stop.is_set():await asyncio.sleep(.01)
            await client.stop_notify(notify)
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
