"""Check-in scheduling, protocol construction and no duplicate submission."""
from copy import deepcopy
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock
from zoneinfo import ZoneInfo
import hashlib
import hmac
import base64
import json
from urllib.parse import quote
import pytest

from custom_components.im_motors.primary.checkin import plan, transaction, CheckinManager
from custom_components.im_motors.primary.checkin_api import CheckinApi, checked_today, signing, send
from custom_components.im_motors.pyim_china.credential_store import key_context, vault_for_path
from custom_components.im_motors.pyim_china.runtime_lock import AccountFileLock
from custom_components.im_motors.trip_api import TripError

NOW = datetime(2026, 11, 20, 3, tzinfo=ZoneInfo('Asia/Shanghai'))
DAY = NOW.date().isoformat()


def record(signed, now=NOW):
    return {'blocks': [{'signTime': DAY, 'signType': 0}] if signed else [],
            'nowTime': str(int(now.timestamp()*1000)), 'signSum': 509}


def run(previous=None, signed=False, after=True, fail=None):
    state = {'attempts': {DAY: {'status': previous}}} if previous else {}
    snapshots = []
    api = Mock()
    records = iter([record(signed), record(after)])
    def call(operation, day=None):
        if operation == fail:
            raise TimeoutError()
        if operation == 'record':
            return next(records)
        return {}
    api.call.side_effect = call
    transaction(None, state, NOW, lambda s:snapshots.append(deepcopy(s)), api)
    return state, snapshots, api


def test_random_bounds_and_restart_stability():
    for offset in (0, 14399):
        state = {}
        target = plan(state, NOW.replace(hour=0), lambda n: min(offset,n-1))
        assert 2 <= target.hour < 6
        assert plan(state, NOW.replace(hour=1), lambda n:0) == target
    state = {}
    target = plan(state, NOW.replace(hour=5,minute=59,second=59), lambda n:n-1)
    assert target.date() == NOW.date()+timedelta(days=1)
    assert 2 <= target.hour < 6


def test_day_rollover_and_already_attempted():
    state = {'attempts': {DAY: {'status': 'checked'}}}
    assert plan(state, NOW, lambda n:0).date() == NOW.date()+timedelta(days=1)
    assert plan({}, NOW.replace(hour=8), lambda n:0).date() == NOW.date()+timedelta(days=1)


def test_already_checked_does_not_post():
    state, snapshots, api = run(signed=True)
    assert state['last_result']['status'] == 'already_checked'
    assert [c.args[0] for c in api.call.call_args_list] == ['info','record']


def test_unsigned_posts_once_after_durable_marker_and_verifies():
    state, snapshots, api = run()
    assert state['last_result']['status'] == 'checked'
    assert any(s['attempts'][DAY]['status'] == 'submitting' for s in snapshots)
    assert [c.args[0] for c in api.call.call_args_list] == ['info','record','check','record']
    transaction(None, state, NOW, Mock(), api)
    assert api.call.call_count == 4


@pytest.mark.parametrize('signed,expected', [(False,'unknown'),(True,'already_checked')])
def test_interrupted_post_only_reads(signed, expected):
    state, _, api = run(previous='submitting',signed=signed)
    assert state['last_result']['status'] == expected
    assert all(c.args[0] != 'check' for c in api.call.call_args_list)


@pytest.mark.parametrize('fail,expected', [('info','error'),('record','error'),('check','unknown')])
def test_failures_do_not_blindly_retry(fail, expected):
    state, _, api = run(fail=fail)
    assert state['last_result']['status'] == expected
    count = api.call.call_count
    transaction(None, state, NOW, Mock(), api)
    assert api.call.call_count == count


def test_success_response_without_record_is_unknown():
    state, _, _ = run(after=False)
    assert state['last_result']['status'] == 'unknown'


def test_outside_window_and_invalid_record_do_not_post():
    api = Mock()
    transaction(None, {}, NOW.replace(hour=8), Mock(), api)
    api.call.assert_not_called()
    with pytest.raises(TripError):checked_today({},DAY)
    api.call.side_effect = [{},record(False,NOW-timedelta(days=1))]
    state = {}
    transaction(None,state,NOW,Mock(),api)
    assert state['last_result']['status'] == 'error'
    assert api.call.call_count == 2


def test_h5_post_signature_includes_body():
    signed = {'x-timestamp':'123','x-app-key':'android-h5','x-nonce':'abc','x-encrypt':'false'}
    url = 'https://m.immotors.com/app/planet/api/v1/checkin/check'
    expected = 'POST&'+url+'&x-app-key=android-h5&x-encrypt=false&x-nonce=abc&x-timestamp=123&{}'
    value = base64.b64encode(hmac.new(b'test-secret',quote(expected,safe='-._~').encode(),hashlib.sha256).digest()).decode()
    assert signing('POST',url,signed,b'test-secret',body='{}') == value


def test_h5_headers_and_transport_allowlist(account):
    transport = Mock(return_value=(json.dumps({'resultCode':'200','data':{}}),{}))
    c = account['client']; api = CheckinApi(c, transport=transport)
    with key_context(c.key_file), AccountFileLock(c.data_dir):
        for op in ('info','record','check'):api.call(op,DAY)
    method,path,headers,body = transport.call_args.args
    assert method == 'POST' and path.endswith('/check') and body == '{}'
    assert headers['x-app-key'] == 'android-h5'
    assert 'deviceId' not in headers
    assert headers['x-original-path'].startswith('https://m.immotors.com/')
    with pytest.raises(TripError):send('POST','/app/planet/api/v1/checkin/re-check',{},'{}')


@pytest.mark.parametrize('role,enabled', [('authorized',True),('primary',False)])
async def test_only_enabled_primary_schedules(hass, account, role, enabled):
    entry = SimpleNamespace(data={'account_type':role},options={'daily_checkin_enabled':enabled})
    manager = CheckinManager(hass,entry,SimpleNamespace(client=account['client']))
    await manager.setup()
    assert manager._task is None and manager._remove_started is None
    await manager.stop()


async def test_persistent_plan_and_started_listener_cleanup(hass, account):
    entry = SimpleNamespace(data={'account_type':'primary'},options={'daily_checkin_enabled':True})
    coordinator = SimpleNamespace(client=account['client'],async_update_listeners=Mock())
    manager = CheckinManager(hass,entry,coordinator)
    manager.now = lambda: NOW
    state = await hass.async_add_executor_job(manager._work,False)
    first = state['next_run']
    again = await hass.async_add_executor_job(manager._work,False)
    assert again['next_run'] == first
    await manager.setup()
    assert manager._remove_started is not None
    await manager.stop()
    assert manager._stopping
    # A new manager reads the original persisted plan instead of redrawing it.
    other = CheckinManager(hass,entry,coordinator)
    other.now = lambda: NOW
    assert (await hass.async_add_executor_job(other._work,False))['next_run'] == first
    await other.stop()


def test_window_closes_during_status_query_no_post():
    state = {}
    api = Mock()
    api.call.side_effect = [{},record(False)]
    client = SimpleNamespace(clock=lambda:int(NOW.replace(hour=6).timestamp()*1000))
    transaction(client,state,NOW,Mock(),api)
    assert state['last_result']['status'] == 'error'
    assert api.call.call_count == 2
