"""Official calls need explicit consent and retain Beijing office-hour limits."""
from datetime import datetime, timezone

import pytest

from test_workbench import client  # noqa: F401
from app import db, official_api_policy as policy, provider


def beijing(value):
    return datetime.fromisoformat(value).replace(tzinfo=policy.SHANGHAI)


BOUNDARIES = [
    ('2026-11-02T08:59:59', False, None),
    ('2026-11-02T09:00:00', True, '2026-11-02T12:00:00+08:00'),
    ('2026-11-03T11:59:59', True, '2026-11-03T12:00:00+08:00'),
    ('2026-11-03T12:00:00', False, None),
    ('2026-11-04T13:59:59', False, None),
    ('2026-11-04T14:00:00', True, '2026-11-04T18:00:00+08:00'),
    ('2026-11-05T17:59:59', True, '2026-11-05T18:00:00+08:00'),
    ('2026-11-06T18:00:00', False, None),
    ('2026-11-07T10:00:00', False, None),
    ('2026-11-08T15:00:00', False, None),
]


@pytest.mark.parametrize('stamp,peak,next_time', BOUNDARIES)
def test_default_blocks_all_hours_including_weekends(client, stamp, peak, next_time):
    state = policy.status(beijing(stamp))
    assert state['allow_official_api'] is False
    assert state['allow_official_peak_hours'] is False
    assert state['blocked'] is True
    assert state['is_peak_hours'] is peak
    assert state['next_allowed_at'] is None
    assert state['timezone'] == 'Asia/Shanghai'
    assert '花费高' in state['warning'] and '缓存命中率非常低' in state['warning']
    assert db.setting(policy.SETTING) is None  # Reading defaults never enables it.


@pytest.mark.parametrize('stamp,blocked,next_time', BOUNDARIES)
def test_general_consent_retains_original_peak_boundaries(client, stamp, blocked, next_time):
    policy.save({'allow_official_api': True})
    state = policy.status(beijing(stamp))
    assert state['allow_official_api'] is True
    assert state['allow_official_peak_hours'] is False
    assert state['blocked'] is blocked
    assert state['is_peak_hours'] is blocked
    assert state['next_allowed_at'] == next_time


def test_utc_date_is_converted_to_beijing_before_weekday_and_window_checks(client):
    policy.save({'allow_official_api': True})
    assert policy.status(datetime(2026, 11, 3, 1, 0, tzinfo=timezone.utc))['blocked']
    assert not policy.status(datetime(2026, 11, 3, 4, 0, tzinfo=timezone.utc))['blocked']
    assert not policy.status(datetime(2026, 11, 6, 19, 0, tzinfo=timezone.utc))['blocked']  # Saturday.
    assert policy.status(datetime(2026, 11, 3, 14, 0))['blocked']  # Explicit naive Beijing time.


@pytest.mark.parametrize('url,official', [
    ('https://api.deepseek.com/v1', True),
    ('https://api.deepseek.com:443/v1/chat/completions', True),
    ('https://API.DEEPSEEK.COM./v1', True),
    ('https://deepseek.com', True),
    ('https://api-cn.deepseek.com/v1', True),
    ('https://new.x5m5x.com', False),
    ('https://notdeepseek.com', False),
    ('https://deepseek.com.example.org', False),
    ('https://api.deepseek.com@relay.example/v1', False),
    ('https://relay.example/v1?upstream=api.deepseek.com', False),
    ('https://relay.example/api.deepseek.com', False),
    ('not a URL', False),
    (None, False),
])
def test_official_destination_matches_the_real_host(url, official):
    assert policy.is_official({'base_url': url, 'model': 'deepseek-flash'}) is official


def test_manual_switches_persist_merge_and_disable_independently(client, monkeypatch):
    monkeypatch.setattr(policy, 'current_time', lambda: beijing('2026-11-03T15:00:00'))
    path = '/api/v1/deepseek/official-api-policy'
    initial = client.get(path)
    assert initial.status_code == 200 and initial.json()['blocked']
    enabled = client.put(path, json={'allow_official_api': True})
    assert enabled.status_code == 200 and enabled.json()['allow_official_api']
    assert enabled.json()['blocked']  # General consent does not bypass peak restrictions.
    assert not enabled.json()['allow_official_peak_hours']
    assert enabled.json()['next_allowed_at'] == '2026-11-03T18:00:00+08:00'
    peak_enabled = client.put(path, json={'allow_official_peak_hours': True})
    assert peak_enabled.status_code == 200 and not peak_enabled.json()['blocked']
    assert peak_enabled.json()['allow_official_api']
    assert client.get(path).json() == peak_enabled.json()
    assert db.setting(policy.SETTING) == {'allow_official_api': True, 'allow_official_peak_hours': True}
    main_disabled = client.put(path, json={'allow_official_api': False})
    assert main_disabled.status_code == 200 and main_disabled.json()['blocked']
    assert main_disabled.json()['allow_official_peak_hours'] is True
    assert main_disabled.json()['next_allowed_at'] is None
    client.put(path, json={'allow_official_api': True})
    peak_disabled = client.put(path, json={'allow_official_peak_hours': False})
    assert peak_disabled.status_code == 200 and peak_disabled.json()['blocked']
    assert peak_disabled.json()['allow_official_api'] is True
    rows = db.rows("SELECT detail FROM audit WHERE action='official_api_policy_changed' ORDER BY rowid")
    assert len(rows) == 5
    assert db.loads(rows[0]['detail'])['allow_official_api_before'] is False
    last = db.loads(rows[-1]['detail'])
    assert last['allow_official_api'] is True and last['allow_official_peak_hours'] is False
    assert last['allow_official_peak_hours_before'] is True


@pytest.mark.parametrize('saved', [
    {'allow_official_peak_hours': True}, {'allow_official_peak_hours': False},
    {'allow_official_api': 1, 'allow_official_peak_hours': True},
    {'allow_official_api': 'true'}, {'allow_official_api': None}, [], True,
])
def test_legacy_or_malformed_settings_never_grant_general_consent(client, saved):
    db.set_setting(policy.SETTING, saved)
    for stamp in ('2026-11-03T10:00:00', '2026-11-03T20:00:00', '2026-11-08T15:00:00'):
        state = policy.status(beijing(stamp))
        assert not state['allow_official_api'] and state['blocked']
        assert state['next_allowed_at'] is None
    assert db.setting(policy.SETTING) == saved


def test_peak_override_alone_cannot_authorize_official_calls(client):
    state = client.put('/api/v1/deepseek/official-api-policy', json={'allow_official_peak_hours': True}).json()
    assert state['allow_official_peak_hours'] is True
    assert state['allow_official_api'] is False and state['blocked']


@pytest.mark.parametrize('body', [{}, {'allow_official_api': 1},
    {'allow_official_api': 'true'}, {'allow_official_api': None}, {'allow_official_api': []},
    {'allow_official_api': True, 'allow_official_peak_hours': 'true'},
    {'allow_official_peak_hours': 1},
    {'allow_official_peak_hours': 'true'}, {'allow_official_peak_hours': None},
    {'allow_official_peak_hours': False, 'timezone': 'UTC'},
    {'allow_official_peak_hours': []}])
def test_api_rejects_non_boolean_and_unknown_fields_without_persisting(client, body):
    response = client.put('/api/v1/deepseek/official-api-policy', json=body)
    assert response.status_code == 422
    assert db.setting(policy.SETTING) is None


@pytest.mark.parametrize('general,peak_override,stamp,code,until', [
    (False, False, '2026-11-03T09:00:00', policy.CODE, None),
    (False, True, '2026-11-03T20:00:00', policy.CODE, None),
    (True, False, '2026-11-03T09:00:00', policy.PEAK_CODE, '2026-11-03T12:00:00+08:00'),
])
def test_block_is_local_known_unsent_without_retry(client, general, peak_override, stamp, code, until):
    policy.save({'allow_official_api': general, 'allow_official_peak_hours': peak_override})
    with pytest.raises(provider.ProviderError) as raised:
        policy.assert_allowed({'base_url': 'https://api.deepseek.com'}, beijing(stamp))
    error = raised.value
    assert not error.unknown and not error.retryable and not error.fallback_allowed
    assert error.code == code
    assert error.details['blocked'] and error.blocked_until == until
    assert error.failure_phase == 'policy_before_http'
    assert error.transport_error_type == ('OfficialAPIPolicy' if not general else 'OfficialAPIPeakHoursPolicy')
    assert policy.WARNING in str(error)
    policy.assert_allowed({'base_url': 'https://relay.example', 'model': 'deepseek-flash'}, beijing(stamp))


def test_explicit_consent_allows_only_the_matching_time_policy(client):
    policy.save({'allow_official_api': True})
    policy.assert_allowed({'base_url': 'https://api.deepseek.com'}, beijing('2026-11-03T18:00:00'))
    policy.save({'allow_official_peak_hours': True})
    policy.assert_allowed({'base_url': 'https://api.deepseek.com'}, beijing('2026-11-03T09:00:00'))


def test_policy_is_included_in_unified_settings_and_routes_do_not_switch_endpoints(client):
    security_before = db.setting('deepseek_pool')
    assert client.get('/api/v1/deepseek/official-api-policy').status_code == 200
    assert client.put('/api/v1/deepseek/official-api-policy', json={'allow_official_api': True}).status_code == 200
    assert db.setting('deepseek_pool') == security_before
    response = client.get('/api/v1/deepseek/settings')
    assert response.status_code == 200
    assert response.json()['official_api_policy']['allow_official_api']
    assert response.json()['official_api_policy']['warning'] == policy.WARNING
