"""Explicit local HTTP actions for the separately owned real BLE car."""
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from car_service import CarError
from car_v83_service import CarService

service = CarService()  # Construction does not start a worker or access Bluetooth.
router = APIRouter(prefix='/car', tags=['car'])


class ConnectBody(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    device_id: str = Field(min_length=1, max_length=512)


class SessionBody(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    session: str = Field(min_length=1, max_length=128)


class IntentBody(SessionBody):
    sequence: int = Field(ge=0, le=9007199254740991)
    direction: Literal['FWD', 'REV'] | None
    steering: Literal['LEFT', 'CENTER', 'RIGHT']
    speed_q8: int = Field(default=1024, ge=128, le=4096)
    route: int = Field(default=0, ge=0, le=8)
    slow_percent: Literal[25, 50, 75] = 50
    steer_percent: int = Field(default=100, ge=10, le=100)


class ProfileBody(SessionBody):
    profile: int = Field(ge=0, le=1)


class BoostBody(SessionBody):
    sequence: int = Field(ge=0, le=9007199254740991)
    peak_q8: int = Field(ge=1024, le=6144)


def _call(function, *args):
    try:
        return function(*args)
    except CarError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@router.get('/status')
def status():
    return service.status()


@router.post('/scan')
def scan():
    return _call(service.scan)


@router.post('/connect')
def connect(body: ConnectBody):
    return _call(service.connect, body.device_id)


@router.post('/intent')
def intent(body: IntentBody):
    if body.route and (body.direction is not None or body.steering != 'CENTER'):
        raise HTTPException(422, '轨迹与手动方向互斥')
    return _call(service.intent, body.session, body.sequence, body.direction, body.steering,
                 body.speed_q8, body.route, body.slow_percent, body.steer_percent)


@router.post('/stop')
def stop(body: SessionBody):
    return _call(service.stop, body.session)


@router.post('/disconnect')
def disconnect(body: SessionBody):
    return _call(service.disconnect, body.session)


@router.post('/profile')
def profile(body: ProfileBody):
    return _call(service.profile, body.session, body.profile)


@router.post('/boost')
def boost(body: BoostBody):
    return _call(service.boost, body.session, body.sequence, body.peak_q8)


@router.post('/brake')
def brake(body: SessionBody):
    return _call(service.brake, body.session)
