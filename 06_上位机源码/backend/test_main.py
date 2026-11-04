import unittest
from unittest.mock import Mock, patch
from fastapi import HTTPException

import main
from host_bridge import BridgeError
from test_host_bridge import fixture


class ApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        config=patch.object(main,'load_config',return_value={'bridge_port':8371,'firmware_variant':'auto'})
        config.start();self.addCleanup(config.stop)

    def test_command_model_accepts_timestamped_drawing_input(self):
        body=dict(action='stroke-move',request_id='r',session='s',stroke_id='p',seq=2,target=1,issued_ms=1700000000000)
        self.assertEqual(main.Command.model_validate(body).model_dump(exclude_unset=True),body)

    async def test_recent_identified_status_avoids_an_extra_round_trip_for_each_draw_point(self):
        import time
        from test_delivery_profiles import snapshot
        cfg={'bridge_port':8371,'firmware_variant':'auto'}
        hub=main.Hub();hub._command_snapshot=(cfg,snapshot('DS13'),time.monotonic())
        client=Mock();client.status.side_effect=AssertionError('fresh DS13 status was already polled')
        client.command.return_value={'state':'accepted'}
        body=main.Command(action='stroke-move',request_id='r',session='fixture',stroke_id='s',seq=2,target=3,issued_ms=1700000000000)
        with patch.object(main,'hub',hub),patch.object(main,'HostClient',return_value=client):
            result=await main.command(body)
        self.assertEqual(result['state'],'accepted');client.status.assert_not_called();client.command.assert_called_once()

    async def test_stale_cached_status_is_never_used_for_a_draw_point(self):
        import time
        from test_delivery_profiles import snapshot
        cfg={'bridge_port':8371,'firmware_variant':'auto'}
        hub=main.Hub();hub._command_snapshot=(cfg,snapshot('DS13'),time.monotonic()-1)
        client=Mock();client.status.return_value=dict(snapshot('DS13'),session='new')
        body=main.Command(action='stroke-move',request_id='r',session='fixture',stroke_id='s',seq=2,target=3,issued_ms=1700000000000)
        with patch.object(main,'hub',hub),patch.object(main,'HostClient',return_value=client):
            with self.assertRaises(HTTPException):await main.command(body)
        client.status.assert_called_once();client.command.assert_not_called()

    async def test_cached_other_firmware_cannot_authorize_ds13_drawing(self):
        import time
        from test_delivery_profiles import snapshot
        cfg={'bridge_port':8371,'firmware_variant':'auto'}
        hub=main.Hub();hub._command_snapshot=(cfg,snapshot('G3_C1'),time.monotonic())
        client=Mock()
        body=main.Command(action='stroke-move',request_id='r',session='fixture',stroke_id='s',seq=2,target=3,issued_ms=1700000000000)
        with patch.object(main,'hub',hub),patch.object(main,'HostClient',return_value=client):
            with self.assertRaises(HTTPException):await main.command(body)
        client.command.assert_not_called()

    async def test_host_drawing_rejection_preserves_its_reason_code(self):
        import time
        from test_delivery_profiles import snapshot
        cfg={'bridge_port':8371,'firmware_variant':'auto'}
        hub=main.Hub();hub._command_snapshot=(cfg,snapshot('DS13'),time.monotonic())
        error=BridgeError('笔画已结束');error.request_rejected=True;error.code='stroke_ended'
        client=Mock();client.command.side_effect=error
        body=main.Command(action='stroke-move',request_id='r',session='fixture',stroke_id='s',seq=2,target=3,issued_ms=1700000000000)
        with patch.object(main,'hub',hub),patch.object(main,'HostClient',return_value=client):
            with self.assertRaises(HTTPException) as caught:await main.command(body)
        self.assertEqual(caught.exception.detail['code'],'stroke_ended')

    def test_cached_status_ages_and_configuration_are_checked(self):
        from test_delivery_profiles import snapshot
        cfg={'bridge_port':8371,'firmware_variant':'auto'}
        hub=main.Hub();hub._command_snapshot=(cfg,snapshot('DS13'),10.0)
        with patch.object(main.time,'monotonic',return_value=10.08):
            result=hub.recent_command_snapshot(cfg)
            self.assertAlmostEqual(result['status_age_s'],.09)
            self.assertIsNone(hub.recent_command_snapshot(dict(cfg,firmware_variant='G3_C1')))
        with patch.object(main.time,'monotonic',return_value=10.09):
            self.assertIsNotNone(hub.recent_command_snapshot(cfg))
        with patch.object(main.time,'monotonic',return_value=10.11):
            self.assertIsNone(hub.recent_command_snapshot(cfg))

    async def test_cached_device_status_still_obeys_500ms_freshness(self):
        import time
        from test_delivery_profiles import snapshot
        cfg={'bridge_port':8371,'firmware_variant':'auto'};status=snapshot('DS13');status['status_age_s']=.45
        hub=main.Hub();hub._command_snapshot=(cfg,status,time.monotonic()-.08)
        client=Mock();body=main.Command(action='stroke-move',request_id='r',session='fixture',stroke_id='s',seq=2,target=3,issued_ms=1700000000000)
        with patch.object(main,'hub',hub),patch.object(main,'HostClient',return_value=client):
            with self.assertRaises(HTTPException):await main.command(body)
        client.command.assert_not_called()

    async def test_explicit_command_is_forwarded_once_and_keeps_host_state(self):
        client=Mock(); client.status.return_value=fixture(); client.command.return_value=dict(state='accepted',request_id='one')
        with patch.object(main,'HostClient',return_value=client):
            result=await main.command(main.Command(action='run',request_id='one'))
        self.assertEqual(result['state'],'accepted')
        client.command.assert_called_once_with(dict(action='run',request_id='one'))

    async def test_v4_command_is_bound_to_the_verified_host_session(self):
        from test_delivery_profiles import snapshot
        client = Mock()
        client.status.return_value = snapshot('V4_USB_BLE')
        client.command.return_value = dict(state='queued', request_id='one')
        with patch.object(main, 'HostClient', return_value=client):
            await main.command(main.Command(action='clear-fault', request_id='one', session='fixture'))
        client.command.assert_called_once_with(dict(action='clear-fault', request_id='one', session='fixture'))

    async def test_v4_command_from_previous_displayed_session_is_not_forwarded(self):
        from test_delivery_profiles import snapshot
        client = Mock(); client.status.return_value = snapshot('V4_USB_BLE')
        with patch.object(main, 'HostClient', return_value=client):
            with self.assertRaises(HTTPException):
                await main.command(main.Command(action='clear-fault', request_id='old', session='previous'))
        client.command.assert_not_called()

    async def test_identity_failure_never_forwards_a_mutation(self):
        client=Mock(); client.status.return_value=dict(fixture(),schema='wrong')
        with patch.object(main,'HostClient',return_value=client):
            with self.assertRaises(HTTPException): await main.command(main.Command(action='run',request_id='one'))
        client.command.assert_not_called()

    async def test_command_timeout_reports_unknown_with_request_id_without_retry(self):
        client=Mock(); client.status.return_value=fixture(); client.command.side_effect=BridgeError('timeout')
        with patch.object(main,'HostClient',return_value=client):
            with self.assertRaises(HTTPException) as context:
                await main.command(main.Command(action='stop',request_id='keep-this-id'))
        self.assertEqual(context.exception.detail['request_id'],'keep-this-id')
        self.assertEqual(context.exception.detail['state'],'indeterminate')
        client.command.assert_called_once()

    async def test_polling_publishes_only_new_samples_and_clears_after_transport_loss(self):
        hub=main.Hub()
        client=Mock(); client.status.return_value=fixture()
        with patch.object(main,'HostClient',return_value=client):
            await hub.poll_once(); await hub.poll_once()
            self.assertEqual(len(hub.samples),2)
            client.status.side_effect=BridgeError('offline')
            await hub.poll_once()
            self.assertFalse(hub.latest['host_online'])
            self.assertIsNone(hub.latest['angle_deg'])
            self.assertEqual(len(hub.samples),0)
            client.status.side_effect=None
            await hub.poll_once()
            self.assertEqual(len(hub.samples),0)
            self.assertIsNone(hub.latest['angle_deg'])

    async def test_v4_unsynced_status_keeps_stop_available_without_rebinding_buffered_compact(self):
        import time
        from test_delivery_profiles import snapshot
        status = dict(snapshot('V4_USB_BLE'), synced=False)
        compact = dict(kind='compact', wire_version=4, variant=4, sequence=1,
                       board_ms=5, control_epoch=5, coherent=True, range_valid=True)
        client = Mock()
        client.status.return_value = status
        client.waveform.return_value = dict(stream='fixture', cursor=1, tail=1, head=1, overrun=False,
            samples=[dict(cursor=1, session='fixture', received_at=time.time(), frame=compact)])
        hub = main.Hub()
        with patch.object(main, 'HostClient', return_value=client):
            await hub.poll_once()
        self.assertTrue(hub.latest['host_online'])
        self.assertEqual(hub.latest['firmware'], 'V4_USB_BLE')
        self.assertFalse(hub.latest['synced'])
        self.assertEqual(len(hub.samples), 0)
        client.waveform.assert_not_called()


if __name__=='__main__': unittest.main()
