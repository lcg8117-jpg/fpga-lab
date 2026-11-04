from __future__ import annotations

import asyncio
from collections import deque
import json
from pathlib import Path
import time
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, StrictInt
from host_bridge import BridgeError, HostClient, TelemetryAdapter, empty_state, identify, validate_action, validate_config
from fd2_telemetry import DELIVERY_PROFILES, V4_R28_PROFILES
from car_api import router as car_router, service as car_service

CONFIG_PATH=Path(__file__).resolve().parent/'config.json'
FD2_DISPLAY_PERIOD=1/30
DS13_COMMAND_SNAPSHOT_MAX_AGE=.1
FD2_FIRMWARES = frozenset(('V1','V2','V3','V4','V5','ADB1', *DELIVERY_PROFILES))
app=FastAPI(title='J280 V1–V5 Showcase',version='3.0.0')
app.include_router(car_router)
app.add_middleware(CORSMiddleware,allow_origins=['http://localhost:5173','http://127.0.0.1:5173'],
                   allow_credentials=True,allow_methods=['*'],allow_headers=['*'])


def load_config():
    return validate_config(json.loads(CONFIG_PATH.read_text(encoding='utf-8')))


class Command(BaseModel):
    model_config=ConfigDict(extra='forbid')
    action: str
    request_id: str
    name: str | None=None
    value: Any=None
    session: str | None=None
    stroke_id: str | None=None
    seq: StrictInt | None=None
    target: StrictInt | None=None
    issued_ms: StrictInt | None=None


class ConfigUpdate(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    bridge_port: int
    firmware_variant: str='auto'


def retention_reference(data, samples, now):
    if (data.get('replay') or {}).get('active') and samples:
        return samples[-1]['received_at']
    return now


class Hub:
    def __init__(self):
        self.clients:set[WebSocket]=set()
        self.latest=empty_state()
        self.samples:deque[dict]=deque(maxlen=64000)
        self.adapter=TelemetryAdapter()
        self.last_error='请启动配套 Host，并在 Host 中显式连接串口'
        self._task=None
        self.wave_cursor=None
        self.wave_stream=None
        self.wave_endpoint=None
        self._command_snapshot=None

    def recent_command_snapshot(self,cfg):
        cached=self._command_snapshot
        if cached is None or cached[0]!=cfg: return None
        _,snapshot,started=cached
        elapsed=time.monotonic()-started
        if not 0<=elapsed<=DS13_COMMAND_SNAPSHOT_MAX_AGE: return None
        result=dict(snapshot)
        for name in ('status_age_s','control_age_s'):
            age=result.get(name)
            if type(age) in (int,float): result[name]=age+elapsed
        return result

    async def read_waveform(self,client,cfg):
        endpoint=(cfg['bridge_port'],cfg['firmware_variant'])
        cursor=self.wave_cursor if endpoint==self.wave_endpoint else None
        stream=self.wave_stream if endpoint==self.wave_endpoint else None
        result=None
        target=None
        # FD2 retains 64000 complete control/diagnostic rows. Stop at the first
        # observed tail instead of chasing a continuously advancing producer.
        for _ in range(128):
            page=await asyncio.to_thread(client.waveform,after=cursor,stream=stream)
            token=page.get('stream'); next_cursor=page.get('cursor'); tail=page.get('tail')
            if (not isinstance(token,str) or not token or type(next_cursor) is not int or
                    type(tail) is not int or not 0<=next_cursor<=tail or not isinstance(page.get('samples'),list)):
                raise BridgeError('Host waveform 游标或样本格式无效')
            changed=stream is not None and token!=stream
            page_rows=list(page['samples'])
            if page.get('overrun') and page_rows:
                # Preserve where a later page lost history, not only a batch-wide flag.
                page_rows[0]=dict(page_rows[0],gap_before=True)
            if result is None or changed:
                result=dict(page,samples=page_rows,overrun=bool(page.get('overrun') or changed))
                target=tail
            else:
                result['samples'].extend(page_rows)
                result.update(cursor=next_cursor,tail=tail,overrun=bool(result['overrun'] or page.get('overrun')))
            if next_cursor>=target:
                return result,endpoint
            if not page['samples'] or (not changed and cursor is not None and next_cursor<=cursor):
                raise BridgeError('Host waveform 游标未推进，保留旧游标以便恢复')
            cursor,stream=next_cursor,token
        raise BridgeError('Host waveform 分页超过只读历史范围，保留旧游标')

    async def broadcast(self,payload):
        message=json.dumps(payload,ensure_ascii=False,allow_nan=False)
        for ws in list(self.clients):
            try: await asyncio.wait_for(ws.send_text(message),timeout=2)
            except Exception: self.clients.discard(ws)

    async def poll_once(self):
        reset=False
        rows=[]
        try:
            cfg=load_config()
            client=HostClient(cfg['bridge_port'])
            status_started=time.monotonic()
            snapshot=await asyncio.to_thread(client.status)
            if cfg!=load_config(): return  # Ignore an old endpoint after a settings change.
            variant=identify(snapshot,cfg['firmware_variant'])
            waveform=None
            v4_waiting = (variant in V4_R28_PROFILES and not snapshot.get('synced') and
                          not (snapshot.get('replay') or {}).get('active'))
            if (variant=='SP24' or variant in FD2_FIRMWARES) and not v4_waiting:
                waveform,endpoint=await self.read_waveform(client,cfg)
                if cfg!=load_config(): return
            data,rows,reset=self.adapter.observe(snapshot,time.time(),cfg['firmware_variant'],waveform=waveform)
            if waveform is not None:
                # Advance only after the entire fetched batch was converted successfully.
                self.wave_cursor,self.wave_stream,self.wave_endpoint=waveform['cursor'],waveform['stream'],endpoint
            self.last_error=None
            self.latest=data
            self._command_snapshot=(dict(cfg),snapshot,status_started)
        except (BridgeError,OSError,ValueError,TypeError) as exc:
            reset=self.latest.get('host_online',False)
            self.latest=empty_state()
            self.last_error=str(exc)
            self._command_snapshot=None
            if reset: self.adapter.invalidate()
        if reset: self.samples.clear()
        self.latest['observed_at']=time.time()
        self.latest['error']=self.last_error
        self.samples.extend(rows)
        retention=120 if self.latest.get('firmware') in FD2_FIRMWARES else 30
        if not (self.latest.get('replay') or {}).get('finished'):
            cutoff=retention_reference(self.latest,self.samples,time.time())-retention
            while self.samples and self.samples[0]['received_at']<cutoff: self.samples.popleft()
        await self.broadcast(dict(type='telemetry',data=self.latest,samples=rows,reset=reset))

    async def loop(self):
        while True:
            started=time.monotonic()
            await self.poll_once()
            if self.latest.get('host_online') and self.latest.get('firmware') in FD2_FIRMWARES:
                # Include HTTP reads, history conversion and WebSocket delivery in
                # the 30Hz period. An overrun yields once; it never queues catch-up polls.
                delay=max(0,FD2_DISPLAY_PERIOD-(time.monotonic()-started))
            else:
                delay=.12 if self.latest.get('host_online') else .5
            await asyncio.sleep(delay)

    async def start(self):
        if not self._task or self._task.done(): self._task=asyncio.create_task(self.loop())

    async def stop(self):
        if self._task:
            self._task.cancel()
            try: await self._task
            except asyncio.CancelledError: pass


hub=Hub()


@app.on_event('startup')
async def startup(): await hub.start()


@app.on_event('shutdown')
async def shutdown():
    try:
        await hub.stop()
    finally:
        await asyncio.to_thread(car_service.shutdown)


@app.get('/')
def root(): return dict(name='J280 Showcase Backend',version='3.0.0')


@app.get('/health')
def health():
    return dict(ok=bool(hub.latest.get('status_fresh')),config=load_config(),last_error=hub.last_error,latest=hub.latest)


@app.get('/snapshot')
def snapshot(): return dict(latest=hub.latest,history=list(hub.samples),error=hub.last_error)


@app.get('/config')
def config(): return load_config()


@app.get('/profile-template')
def profile_template():
    cfg=load_config()
    variant=cfg['firmware_variant'] if cfg['firmware_variant']!='auto' else hub.latest.get('firmware')
    if variant not in ('SA21','SP24'):
        raise HTTPException(409,'请先选择或识别支持自适应配置的 SA21 / SP24 固件')
    directory='sp24/gesture' if variant=='SP24' else 'adaptive'
    path=CONFIG_PATH.parent.parent/'host'/directory/'host/machine_profile.template.json'
    return JSONResponse(json.loads(path.read_text(encoding='utf-8-sig')),
                        headers={'Content-Disposition':'attachment; filename="machine_profile.template.json"'})


@app.get('/presets')
def presets():
    root=CONFIG_PATH.parent.parent/'host/sp24/presets'
    names=('field_baseline','damping_d56','slow_i2','field_swing70_230','angle_d80','angle_i780')
    def read(name):
        return json.loads((root/(name+'.json')).read_text(encoding='utf-8-sig'))
    return dict(presets=[read(name) for name in names],engineering_profile=read('e_engineering_shadow'))


@app.post('/config')
async def update_config(body:ConfigUpdate):
    try: cfg=validate_config(body.model_dump())
    except BridgeError as exc: raise HTTPException(400,str(exc)) from exc
    temp=CONFIG_PATH.with_suffix('.json.tmp')
    temp.write_text(json.dumps(cfg,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    temp.replace(CONFIG_PATH)
    hub.adapter=TelemetryAdapter(); hub.samples.clear(); hub.latest=empty_state()
    hub.wave_cursor=hub.wave_stream=hub.wave_endpoint=None
    hub.last_error='连接设置已更新，等待对应 Host'
    await hub.broadcast(dict(type='telemetry',data=hub.latest,samples=[],reset=True))
    return dict(ok=True,config=cfg)


@app.post('/command')
async def command(body:Command):
    cfg=load_config()
    client=HostClient(cfg['bridge_port'])
    payload=body.model_dump(exclude_unset=True)
    try:
        status=hub.recent_command_snapshot(cfg) if payload.get('action','').startswith('stroke-') else None
        if status is None: status=await asyncio.to_thread(client.status)
        validate_action(payload,status,cfg['firmware_variant'])
    except BridgeError as exc:
        raise HTTPException(409,dict(request_id=body.request_id,state='rejected',message=str(exc))) from exc
    try:
        # Exactly one POST: never retry an action after a network or ACK timeout.
        result=await asyncio.to_thread(client.command,payload)
        return {k:v for k,v in result.items() if k!='fingerprint'}
    except BridgeError as exc:
        if getattr(exc,'request_rejected',False):
            detail=dict(request_id=body.request_id,state='rejected',message=str(exc))
            if getattr(exc,'code',None): detail['code']=exc.code
            raise HTTPException(409,detail) from exc
        raise HTTPException(502,dict(request_id=body.request_id,state='indeterminate',
                                     message=str(exc)+'；结果未知，请保留 ID 查询，未重发')) from exc


@app.get('/requests/{request_id}')
async def request_result(request_id:str):
    if not 1<=len(request_id)<=128: raise HTTPException(400,'请求 ID 无效')
    try:
        result=await asyncio.to_thread(HostClient(load_config()['bridge_port']).result,request_id)
        return {k:v for k,v in result.items() if k!='fingerprint'}
    except BridgeError as exc: raise HTTPException(502,str(exc)) from exc


@app.get('/gowin')
async def gowin_points(after: int=0, stream: str | None=None):
    if after < 0 or (stream is not None and len(stream) > 128):
        raise HTTPException(400, '映射游标无效')
    cfg=load_config()
    client=HostClient(cfg['bridge_port'])
    try:
        status=await asyncio.to_thread(client.status)
        if identify(status,cfg['firmware_variant']) != 'G3_C1':
            raise BridgeError('当前 Host 不是 G3 C1')
        page=await asyncio.to_thread(client.gowin,after,stream)
        if cfg != load_config() or page.get('stream') != status.get('session'):
            raise BridgeError('映射会话已变更，请重新读取')
        return page
    except BridgeError as exc:
        raise HTTPException(409,str(exc)) from exc


@app.websocket('/ws')
async def websocket_endpoint(ws:WebSocket):
    await ws.accept()
    hub.clients.add(ws)
    try:
        # Snapshot samples are loaded over /snapshot; don't replay them here.
        await ws.send_json(dict(type='telemetry',data=hub.latest,samples=[],reset=False))
        while True: await ws.receive_text()
    except WebSocketDisconnect: pass
    finally: hub.clients.discard(ws)
