"""Account-role gating and client identity survive setup and refresh."""
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from test_ha import create_entry
from test_sms_login import backend, setup_send, read, save, PHONE, CODE
from custom_components.im_motors.config_flow import ImMotorsConfigFlow, account_input, access_input
from custom_components.im_motors.const import account_type
from custom_components.im_motors.authorized.trips import TripManager
from custom_components.im_motors.pyim_china.account_auth import prepare_refresh, CredentialError
from custom_components.im_motors.pyim_china.credential_store import key_context
from custom_components.im_motors.pyim_china.local_production_config import load_local_production_config
from custom_components.im_motors.pyim_china.standalone_refresh_probe import load_session, refresh_once
from conftest import response
import voluptuous as vol


@pytest.mark.parametrize('data,options,expected', [
    ({}, {}, True),
    ({'account_type': 'primary'}, {}, False),
    ({'account_type': 'authorized'}, {'account_type': 'primary'}, False),
    ({'account_type': 'primary'}, {'account_type': 'authorized'}, True),
])
async def test_role_gates_observation(data, options, expected):
    entry = SimpleNamespace(data=data, options=dict(options, trip_history_enabled=True))
    manager = TripManager.__new__(TripManager)
    manager.entry = entry
    assert manager.enabled is expected
    if not expected:
        # No coordinator/store are needed: primary accounts return before sampling.
        await manager.observe(SimpleNamespace(telemetry={'vehicle': object()}))


def test_invalid_role_and_access_are_rejected():
    for role in ('owner', '', None, True):
        with pytest.raises(vol.Invalid):
            account_input({'account_type': role})
    with pytest.raises(vol.Invalid):
        access_input({'access_mode': 'primary', 'accept_control_risk': False})
    assert account_type(SimpleNamespace(options={}, data={})) == 'authorized'


async def test_primary_import_and_options_switch(hass, account):
    flow = ImMotorsConfigFlow()
    flow.hass = hass
    flow.context = {'source': 'user'}
    result = await flow.async_step_import({'data_dir': str(account['data']),
        'key_file': str(account['key']), 'account_type': 'primary'})
    assert result['data']['account_type'] == 'primary'
    entry = await create_entry(hass, account)
    with patch.object(hass.config_entries, 'async_setup', return_value=True):
        await hass.config_entries.async_add(entry)
    for role in ('primary', 'authorized'):
        form = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(form['flow_id'], {
            'account_type': role, 'trip_history_enabled': True})
        assert result['type'] == 'create_entry'
        assert account_type(entry) == role
        assert entry.options['trip_history_enabled'] is (role == 'authorized')


@pytest.mark.parametrize('client_type', ['APP', 'WXAPP'])
def test_refresh_keeps_login_client_type(account, client_type):
    sms = backend(account)
    setup_send(account)
    sms.start(PHONE)
    if client_type == 'APP':
        # A challenge saved by an older release has no client_type field.
        state = read(account)
        state.pop('client_type')
        save(account, state, 'ha-login.imvault')
    sms.login(CODE)
    with key_context(account['key']):
        _, credentials = load_session(account['data'] / 'session.imvault')
        config = load_local_production_config(account['data'] / 'production.imvault')
        request = prepare_refresh(config, credentials, now_ms=account['now'])
        assert json.loads(request.body)['clientType'] == client_type
        account['transport'].send.side_effect = None
        account['transport'].send.return_value = response({'accessToken': 'SYNTHETIC-ROTATED'})
        refresh_once(account['data'] / 'session.imvault', account['data'] / 'refresh.imvault',
            load_config=lambda: config, transport_factory=account['factory'])
        session, updated = load_session(account['data'] / 'session.imvault')
        assert session['client_type'] == updated.client_type == client_type
        assert updated.access_token == 'SYNTHETIC-ROTATED'


def test_legacy_session_defaults_app_and_invalid_type_stops(account):
    with key_context(account['key']):
        _, credentials = load_session(account['data'] / 'session.imvault')
        assert credentials.client_type == 'APP'
    save(account, dict(account['session'], client_type='invalid'))
    with key_context(account['key']), pytest.raises(CredentialError):
        load_session(account['data'] / 'session.imvault')
    account['transport'].send.assert_not_called()
