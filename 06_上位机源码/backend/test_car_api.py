import asyncio
import json
import unittest
from unittest.mock import Mock, patch

from fastapi import FastAPI

try:
    import car_api
except ImportError:
    car_api = None


async def request(app, path, payload=None, method='POST'):
    """Small ASGI-only HTTP fixture; no live server or optional httpx dependency."""
    body = b'' if payload is None else json.dumps(payload).encode('utf-8')
    scope = dict(type='http', asgi=dict(version='3.0'), http_version='1.1',
                 method=method, scheme='http', path=path, raw_path=path.encode(),
                 root_path='', query_string=b'', server=('fixture', 80),
                 client=('127.0.0.1', 1), headers=[(b'content-type', b'application/json')])
    messages = []
    async def receive():
        return dict(type='http.request', body=body, more_body=False)
    async def send(message):
        messages.append(message)
    await app(scope, receive, send)
    return messages[0]['status'], json.loads(b''.join(item.get('body', b'') for item in messages[1:]))


class CarApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.assertIsNotNone(car_api, '缺少独立 /car HTTP 接口')
        self.app = FastAPI()
        self.app.include_router(car_api.router)
        self.fake = Mock()
        self.fake.status.return_value = dict(connected=False, session=None)
        self.fake.scan.return_value = dict(devices=[])
        self.fake.connect.return_value = dict(connected=True, session='current')
        self.fake.intent.return_value = dict(accepted=True)
        self.fake.stop.return_value = dict(connected=True, session='rotated')
        self.fake.disconnect.return_value = dict(connected=False, session=None)
        self.patcher = patch.object(car_api, 'service', self.fake)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    async def test_status_read_does_not_connect_and_scan_is_explicit(self):
        code, body = await request(self.app, '/car/status', method='GET')
        self.assertEqual((code, body['connected']), (200, False))
        self.fake.scan.assert_not_called()
        self.fake.connect.assert_not_called()
        self.assertEqual(await request(self.app, '/car/scan'), (200, dict(devices=[])))

    async def test_connect_stop_disconnect_forward_one_explicit_call(self):
        self.assertEqual((await request(self.app, '/car/connect', dict(device_id='chosen')))[0], 200)
        self.fake.connect.assert_called_once_with('chosen')
        code, body = await request(self.app, '/car/stop', dict(session='current'))
        self.assertEqual((code, body['session']), (200, 'rotated'))
        self.fake.stop.assert_called_once_with('current')
        await request(self.app, '/car/disconnect', dict(session='rotated'))
        self.fake.disconnect.assert_called_once_with('rotated')

    async def test_intent_requires_strict_session_sequence_and_allowed_controls(self):
        valid = dict(session='current', sequence=1, direction='FWD', steering='LEFT')
        self.assertEqual((await request(self.app, '/car/intent', valid))[0], 200)
        self.fake.intent.assert_called_once_with('current', 1, 'FWD', 'LEFT', 1024, 0, 50, 100)
        for changes in (dict(sequence=True), dict(sequence='2'), dict(sequence=-1),
                        dict(direction='FAST'), dict(steering='LOCK'), dict(session=''),
                        dict(sequence=9007199254740992), dict(speed=10)):
            with self.subTest(changes=changes):
                self.assertEqual((await request(self.app, '/car/intent', valid | changes))[0], 422)
        self.assertEqual(self.fake.intent.call_count, 1)

    async def test_v83_routes_strict_and_once(self):
        for path, payload, expected in (
            ('profile', dict(session='current', profile=1), ('current', 1)),
            ('boost', dict(session='current', sequence=2, peak_q8=2048), ('current', 2, 2048)),
            ('brake', dict(session='current'), ('current',))):
            getattr(self.fake,path).return_value=dict(accepted=True)
            self.assertEqual((await request(self.app,'/car/'+path,payload))[0],200)
            getattr(self.fake,path).assert_called_once_with(*expected)
        for payload in (dict(session='current',profile=True),dict(session='current',profile=2)):
            self.assertEqual((await request(self.app,'/car/profile',payload))[0],422)
        valid=dict(session='current',sequence=3,direction=None,steering='CENTER',route=1)
        self.assertEqual((await request(self.app,'/car/intent',valid))[0],200)
        for changes in (dict(direction='FWD'),dict(steering='LEFT'),dict(speed_q8=127),dict(slow_percent=True),dict(steer_percent=101)):
            self.assertEqual((await request(self.app,'/car/intent',valid|changes))[0],422)

    async def test_service_failure_preserves_chinese_reason_and_no_retry(self):
        from car_service import CarError
        self.fake.stop.side_effect = CarError('停车未确认：fixture 超时', 502)
        code, body = await request(self.app, '/car/stop', dict(session='current'))
        self.assertEqual(code, 502)
        self.assertIn('停车未确认', body['detail'])
        self.fake.stop.assert_called_once()

    async def test_application_shutdown_always_closes_car_worker(self):
        import main
        with patch.object(main.hub, 'stop') as stop_host, patch.object(main, 'car_service') as service:
            await main.shutdown()
        stop_host.assert_awaited_once()
        service.shutdown.assert_called_once()


if __name__ == '__main__':
    unittest.main()
