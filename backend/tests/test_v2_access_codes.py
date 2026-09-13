"""Synthetic credentials only; no operator passphrase or remote requests."""
import unicodedata
from urllib.parse import quote

import pytest

from backend.tests.test_v2_personal_http import request, server_factory
from backend.v2.models import ValidationError
from backend.v2.security import AccessPolicy, access_code_header_value

SYNTHETIC_CODE = 'test-' + chr(0xAC01) + chr(0xB098) + '-42'
OLD_TOKEN = 'synthetic-old-user-token-' + 'a' * 32
WORKER_TOKEN = 'synthetic-worker-token-' + 'b' * 32
CAPS = {'worker_id': 'synthetic-worker-identity-00000001', 'protocol': 1,
        'search_source': 'suumo', 'import_sources': ['chintai', 'yahoo_realestate']}


@pytest.fixture(autouse=True)
def source_configuration(monkeypatch):
    monkeypatch.setenv('HOUSE_EVALUATOR_SEARCH_SOURCE', 'suumo')
    monkeypatch.setenv('HOUSE_EVALUATOR_IMPORT_SOURCES', 'chintai,yahoo_realestate')


def test_personal_unicode_is_nfc_encoded_to_fixed_ascii_and_not_decoded_on_input():
    decomposed = unicodedata.normalize('NFD', SYNTHETIC_CODE)
    expected = quote(SYNTHETIC_CODE, safe="~()*!.'-_", encoding='utf-8')
    assert expected.isascii() and expected != SYNTHETIC_CODE
    assert access_code_header_value(decomposed, allow_passphrase=True) == expected
    policy = AccessPolicy(decomposed, allow_passphrase=True)
    assert policy.authenticated('Bearer ' + expected)
    assert not policy.authenticated('Bearer ' + SYNTHETIC_CODE)
    assert not policy.authenticated('Bearer ' + quote(expected, safe=''))
    assert not policy.authenticated('Bearer ' + expected.lower())


def test_non_ascii_encodes_entire_code_with_encode_uri_component_safe_characters():
    value = chr(0xAC00) + "/?%+=:#[]~()*!.'-_"
    wire = access_code_header_value(value, allow_passphrase=True)
    assert wire == "%EA%B0%80%2F%3F%25%2B%3D%3A%23%5B%5D~()*!.'-_"
    assert AccessPolicy(value, allow_passphrase=True).authenticated('Bearer ' + wire)


@pytest.mark.parametrize('code', ['x', 'short-test-code', '%EA%B0%80', "ascii/?%+=:#[]~()*!.'-_", OLD_TOKEN])
def test_ascii_personal_credentials_remain_literal_even_when_percent_shaped(code):
    assert access_code_header_value(code, allow_passphrase=True) == code
    policy = AccessPolicy(code, allow_passphrase=True)
    assert policy.authenticated('Bearer ' + code)
    assert not policy.authenticated('Bearer ' + code + 'wrong')


def test_existing_long_ascii_strong_and_personal_headers_are_identical():
    for code in (OLD_TOKEN, 'A' * 31 + '%', 'X' * 250 + '/+=!_-'):
        assert access_code_header_value(code) == code
        assert access_code_header_value(code, allow_passphrase=True) == code
        assert AccessPolicy(code).authenticated('Bearer ' + code)
        assert AccessPolicy(code, allow_passphrase=True).authenticated('Bearer ' + code)


@pytest.mark.parametrize('code', ['', 'x' * 257, 'space code', ' leading', 'trailing ', 'line\nfeed',
                                 'tab\there', 'null\x00byte', 'delete\x7f', 'nbsp\u00a0x',
                                 'format\u200dx', 'bidi\u202ex', 'surrogate\ud800', None, True, 123])
def test_personal_passphrase_validation_rejects_disallowed_characters_without_echo(code):
    with pytest.raises(ValidationError) as error:
        access_code_header_value(code, allow_passphrase=True)
    assert str(error.value) == '개인 접속 코드는 공백·제어 문자 없는 1~256자로 설정하세요.'


@pytest.mark.parametrize('code', ['short', SYNTHETIC_CODE, 'A' * 31, 'A' * 257, 'A' * 32 + ' ', 'A' * 32 + '\n'])
def test_default_policy_stays_strong_ascii_only(code):
    with pytest.raises(ValidationError): AccessPolicy(code)
    with pytest.raises(ValidationError): access_code_header_value(code)


def test_nfc_length_limits_and_unprotected_local_mode_remain_consistent():
    normalized = chr(0xAC00) * 256
    assert len(unicodedata.normalize('NFD', normalized)) > 256
    wire = access_code_header_value(unicodedata.normalize('NFD', normalized), allow_passphrase=True)
    assert AccessPolicy(normalized, allow_passphrase=True).authenticated('Bearer ' + wire)
    assert not AccessPolicy(None, allow_passphrase=True).protected
    assert AccessPolicy(None, allow_passphrase=True).authenticated(None)


def test_http_personal_accepts_encoded_unicode_and_rejects_wrong_old_missing_code(server_factory):
    server = server_factory(access_token=SYNTHETIC_CODE)
    wire = access_code_header_value(SYNTHETIC_CODE, allow_passphrase=True)
    assert request(server, 'GET', '/api/v2/personal/options', token=wire)[0] == 200
    for wrong in (None, 'wrong-test-code', OLD_TOKEN, quote(wire, safe='')):
        status, body = request(server, 'GET', '/api/v2/personal/options', token=wrong)
        assert status == 401 and body['error'] == 'access_required'
        assert SYNTHETIC_CODE not in str(body) and wire not in str(body)


def test_http_short_ascii_and_existing_long_ascii_both_work_in_personal_mode(server_factory):
    for code in ('synthetic-short', OLD_TOKEN):
        server = server_factory(access_token=code)
        assert request(server, 'GET', '/api/v2/personal/options', token=code)[0] == 200
        assert request(server, 'GET', '/api/v2/personal/options', token=code + 'old')[0] == 401


def test_worker_and_personal_credentials_cannot_cross_api_roles(server_factory):
    server = server_factory(execution_mode='worker', access_token=SYNTHETIC_CODE, worker_token=WORKER_TOKEN)
    wire = access_code_header_value(SYNTHETIC_CODE, allow_passphrase=True)
    assert request(server, 'GET', '/api/v2/personal/options', token=wire)[0] == 200
    assert request(server, 'POST', '/api/v2/worker/claim', CAPS, token=WORKER_TOKEN)[0] == 200
    assert request(server, 'POST', '/api/v2/worker/claim', CAPS, token=wire)[0] == 401
    assert request(server, 'GET', '/api/v2/personal/options', token=WORKER_TOKEN)[0] == 401


@pytest.mark.parametrize('user_code', [OLD_TOKEN, SYNTHETIC_CODE * 2])
def test_worker_user_collision_is_checked_after_unicode_wire_encoding(server_factory, user_code):
    wire = access_code_header_value(user_code, allow_passphrase=True)
    assert 32 <= len(wire) <= 256
    with pytest.raises(ValidationError, match='서로 다른'):
        server_factory(execution_mode='worker', access_token=user_code, worker_token=wire)


@pytest.mark.parametrize('settings', [
    {'personal_enabled': False, 'access_token': SYNTHETIC_CODE},
    {'pilot_mode': True, 'access_token': SYNTHETIC_CODE},
    {'pilot_mode': True, 'access_token': 'short'},
    {'execution_mode': 'worker', 'access_token': SYNTHETIC_CODE, 'worker_token': 'short'},
    {'execution_mode': 'worker', 'access_token': SYNTHETIC_CODE, 'worker_token': SYNTHETIC_CODE},
])
def test_personal_opt_in_cannot_weaken_worker_or_pilot(server_factory, settings):
    with pytest.raises(ValidationError, match='ASCII'):
        server_factory(**settings)


def test_invalid_credential_requests_do_not_consume_personal_authenticated_quota(server_factory):
    server = server_factory(access_token=SYNTHETIC_CODE, requests_per_minute=1)
    wire = access_code_header_value(SYNTHETIC_CODE, allow_passphrase=True)
    assert request(server, 'GET', '/api/v2/personal/options', token='wrong')[0] == 401
    assert request(server, 'GET', '/api/v2/personal/options', token='wrong')[0] == 429
    assert request(server, 'GET', '/api/v2/personal/options', token=wire)[0] == 200
