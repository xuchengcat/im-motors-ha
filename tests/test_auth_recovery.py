"""Regressions for abandoned SMS flows and transient cloud reads."""
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from conftest import VIN, response
from test_client import save_session
from custom_components.im_motors.pyim_china.client import (
    AccountClient, ClientFailure, TemporaryReadFailure)
from custom_components.im_motors.pyim_china.credential_store import key_context, vault_for_path
from custom_components.im_motors.pyim_china.restricted_transport import (
    RestrictedHttpsTransport, RetryableReadError, TransportError)


def load_fault(account):
    with key_context(account['key']):
        return vault_for_path(account['data'] / 'ha-fault.imvault')._load_payload()


def test_abandoned_sms_challenge_does_not_block_valid_session(account):
    with key_context(account['key']):
        vault_for_path(account['data'] / 'ha-login.imvault')._save_payload({
            'kind': 'ha-sms-login', 'schema_version': 1, 'status': 'awaiting_code',
            'sent_time_ms': account['now'] - 600000})
    assert len(account['client'].update().vehicles) == 1
    with key_context(account['key']):
        assert vault_for_path(account['data'] / 'ha-login.imvault')._load_payload()['status'] == 'challenge_expired'


@pytest.mark.parametrize('status,age', [
    ('awaiting_code', 599999), ('awaiting_code', -1),
    ('login_attempted', 700000), ('request_unknown', 700000),
    ('login_response_received', 700000)])
def test_active_or_uncertain_login_remains_blocked(account, status, age):
    with key_context(account['key']):
        vault_for_path(account['data'] / 'ha-login.imvault')._save_payload({
            'kind': 'ha-sms-login', 'schema_version': 1, 'status': status,
            'sent_time_ms': account['now'] - age})
    with pytest.raises(ClientFailure):
        account['client'].update()
    account['transport'].send.assert_not_called()


def test_transient_read_backoff_survives_restart_and_recovers(account):
    account['transport'].send.side_effect = RetryableReadError('synthetic')
    with pytest.raises(TemporaryReadFailure):
        account['client'].update()
    assert load_fault(account)['retry_at_ms'] == account['now'] + 60000
    restarted = AccountClient(account['data'], account['key'],
        transport_factory=account['factory'], clock=account['clock'])
    with pytest.raises(TemporaryReadFailure):
        restarted.update()
    assert account['transport'].send.call_count == 1
    account['clock'].return_value = account['now'] + 60000
    with pytest.raises(TemporaryReadFailure):
        restarted.update()
    assert load_fault(account)['retry_at_ms'] == account['now'] + 180000
    account['clock'].return_value = account['now'] + 180000
    account['transport'].send.side_effect = None
    assert len(restarted.update().vehicles) == 1
    assert not (account['data'] / 'ha-fault.imvault').exists()


def test_refresh_still_runs_during_read_backoff(account):
    account['transport'].send.side_effect = RetryableReadError('synthetic')
    with pytest.raises(TemporaryReadFailure):
        account['client'].update()
    save_session(account, suggest_refresh_time=account['now'] - 1)
    account['transport'].send.side_effect = None
    account['transport'].send.return_value = response({
        'accessToken': 'SYNTHETIC-NEW', 'refreshToken': 'SYNTHETIC-ROTATED',
        'expirationTime': account['now'] + 7200000,
        'suggestRefreshTime': account['now'] + 3600000})
    with pytest.raises(TemporaryReadFailure):
        account['client'].update()
    assert account['transport'].send.call_count == 2
    assert account['transport'].send.call_args.args[0].url.endswith('/refresh-token')
    assert load_fault(account)['reason'] == 'temporary_read_failure'


@pytest.mark.parametrize('status,retryable', [(408, True), (429, True),
    (500, True), (503, True), (401, False), (403, False), (302, False)])
def test_only_transient_get_statuses_are_retryable(status, retryable):
    connection = Mock()
    connection.getresponse.return_value.status = status
    request = SimpleNamespace(method='GET',
        url='https://mh.immotors.com/app/vus/v3/isc/scene/vehicleList', headers={})
    with patch('http.client.HTTPSConnection', return_value=connection):
        with pytest.raises(TransportError) as exc:
            RestrictedHttpsTransport(enable_isc_vehicles=True).send(request)
    assert isinstance(exc.value, RetryableReadError) is retryable


def test_network_failure_retry_is_get_only():
    for method, path, body in [('GET', '/app/vus/v3/isc/scene/vehicleList', None),
            ('POST', '/app/login/user/authorize/refresh-token', b'{}')]:
        connection = Mock()
        connection.request.side_effect = OSError('synthetic')
        request = SimpleNamespace(method=method, url='https://mh.immotors.com' + path,
            headers={}, body=body)
        with patch('http.client.HTTPSConnection', return_value=connection):
            with pytest.raises(TransportError) as exc:
                RestrictedHttpsTransport(enable_isc_vehicles=True).send(request)
        assert isinstance(exc.value, RetryableReadError) is (method == 'GET')
