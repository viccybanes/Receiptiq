"""Synthetic onboarding checks; no real workspace, credential, or cloud request."""

import asyncio
import base64
import io
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from PIL import Image
from test_google_vision import KEY
from test_google_vision import google as google_fixture
from test_workflow import client as client_fixture
from test_workflow import ready_receipt

from app import google_vision
from app.google_key import KEY_FILE
from app.google_setup import TEST_MODEL, google_setup_status

client = client_fixture
google = google_fixture
SAMPLE = {"supplier": "RECEIPTOR SAMPLE", "receipt_number": "TEST-001", "receipt_date": "2026-01-01",
          "currency": "PHP", "total": "20.00", "visible_item_count": 1,
          "items": [{"description": "Test item", "quantity": "2", "unit": "pcs",
                     "unit_price": "10.00", "line_total": "20.00"}]}
TEST_URL = '/api/v1/vision/google-key/test'
SETUP_URL = '/api/v1/vision/google-setup'


def workspace_snapshot(root):
    # Windows deliberately denies reads of the app's held workspace lock.
    return {path.relative_to(root): path.read_bytes() for path in root.rglob('*')
            if path.is_file() and path.name != 'workspace.lock'}


def test_ephemeral_key_checks_image_without_mutating_workspace(client, google, caplog):
    receipt, _ = ready_receipt(client)
    client.delete('/api/v1/vision/google-key')
    before = workspace_snapshot(client.store.root)
    google['result'] = SAMPLE
    response = client.post(TEST_URL, json={'api_key': ' ' + KEY + ' '})
    assert response.status_code == 200, response.text
    assert response.json() == {'ok': True, 'model': TEST_MODEL}
    assert workspace_snapshot(client.store.root) == before
    assert client.store.receipt(receipt['id'])['data'] == receipt['data']
    assert not (client.store.root / KEY_FILE).exists()
    assert google_setup_status(client.store.root) == {'status': 'pending'}
    assert len(google['requests']) == 1
    request = google['requests'][0]
    assert request.url.host == 'generativelanguage.googleapis.com'
    assert request.url.scheme == 'https' and request.url.path.endswith(TEST_MODEL + ':generateContent')
    assert request.headers['x-goog-api-key'] == KEY
    body = json.loads(request.content)
    parts = body['contents'][0]['parts']
    assert len(parts) == 2 and parts[1]['inlineData']['mimeType'] == 'image/jpeg'
    with Image.open(io.BytesIO(base64.b64decode(parts[1]['inlineData']['data']))) as image:
        assert image.size == (720, 420) and image.format == 'JPEG'
    assert 'NORTHSIDE MARKET' not in request.content.decode()
    assert 'responseJsonSchema' in body['generationConfig']
    assert KEY not in response.text + caplog.text


def test_saved_key_can_be_tested_while_cloud_is_disabled(client, google):
    client.patch('/api/v1/vision/settings', json={'provider': 'local'})
    before = workspace_snapshot(client.store.root)
    google['result'] = SAMPLE
    assert client.post(TEST_URL, json={}).status_code == 200
    assert workspace_snapshot(client.store.root) == before
    assert google['requests'][0].headers['x-goog-api-key'] == KEY


@pytest.mark.parametrize('status', [302, 400, 401, 403, 404, 429, 503])
def test_provider_failures_are_sanitized_and_do_not_change_saved_key(client, google, caplog, status):
    before = workspace_snapshot(client.store.root)
    google['status'] = status
    result = client.post(TEST_URL, json={'api_key': KEY + '-replacement'})
    assert result.status_code == 422
    assert KEY not in result.text + caplog.text and 'private provider error' not in result.text
    assert workspace_snapshot(client.store.root) == before
    assert len(google['requests']) == 1  # No redirect, fallback, or automatic retry.


@pytest.mark.parametrize('reply', ['empty', 'wrong_total', 'incomplete', 'malformed', 'blocked'])
def test_receiving_any_response_is_not_a_successful_image_test(client, google, reply):
    google['result'] = SAMPLE
    if reply == 'empty':
        google['result'] = {}
    elif reply == 'wrong_total':
        google['result'] = {**SAMPLE, 'total': '999.00'}
    elif reply == 'incomplete':
        google['finish'] = 'MAX_TOKENS'
    elif reply == 'malformed':
        google['content'] = KEY
    else:
        google['reply'] = {'promptFeedback': {'blockReason': 'OTHER'}}
    result = client.post(TEST_URL, json={'api_key': KEY})
    assert result.status_code == 422 and KEY not in result.text


@pytest.mark.parametrize('body', [{'api_key': KEY + '\nsecret'}, {'api_key': [KEY]},
                                 {'api_key': KEY, 'image': KEY}, {'api_key': ''}, {'api_key': 'x' * 5000}])
def test_secret_validation_does_not_echo_input(client, google, body):
    response = client.post(TEST_URL, json=body)
    assert response.status_code == 422 and KEY not in response.text
    assert not google['requests']


def test_missing_saved_key_never_calls_google(client):
    result = client.post(TEST_URL, json={})
    assert result.status_code == 422 and 'key' in result.json()['detail']


@pytest.mark.parametrize('exception', [httpx.ConnectError(KEY), httpx.ReadTimeout(KEY)])
def test_network_errors_are_safe(client, google, caplog, exception):
    google['exception'] = exception
    response = client.post(TEST_URL, json={'api_key': KEY})
    assert response.status_code == 422 and KEY not in response.text + caplog.text


def test_setup_request_is_bounded_to_45_seconds(client, google, monkeypatch):
    timeouts = []
    original_timeout = asyncio.timeout

    def short_timeout(seconds):
        timeouts.append(seconds)
        return original_timeout(.01)

    monkeypatch.setattr(google_vision.asyncio, 'timeout', short_timeout)
    google['delay'] = .5
    response = client.post(TEST_URL, json={'api_key': KEY})
    assert response.status_code == 422 and 'timed out' in response.text
    assert timeouts == [45]


def test_only_one_sample_test_runs_at_a_time_and_lock_releases(client, google):
    entered = threading.Event()
    google.update(result=SAMPLE, delay=.3, on_request=entered.set)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(client.post, TEST_URL, json={'api_key': KEY})
        assert entered.wait(3)
        assert client.post(TEST_URL, json={'api_key': KEY}).status_code == 409
        assert first.result(timeout=3).status_code == 200
    google['delay'] = 0
    assert client.post(TEST_URL, json={'api_key': KEY}).status_code == 200


def test_guide_state_persists_without_changing_other_preferences(client):
    path = client.store.root / 'settings.json'
    path.write_text(json.dumps({'other': {'retained': True}, 'vision': {'amount_first': True}}), encoding='utf-8')
    assert client.get(SETUP_URL).json() == {'status': 'pending'}
    assert client.post(SETUP_URL, json={'status': 'completed'}).status_code == 422
    assert client.post(SETUP_URL, json={'status': 'skipped'}).json() == {'status': 'skipped'}
    assert google_setup_status(client.store.root) == {'status': 'skipped'}
    values = json.loads(path.read_text(encoding='utf-8'))
    assert values['other'] == {'retained': True} and values['vision'] == {'amount_first': True}


def test_completed_guide_requires_enabled_google_and_retains_preferences(client, google):
    client.patch('/api/v1/vision/settings', json={'capture_regions': True})
    settings = client.get('/api/v1/vision/settings').json()
    assert client.post(SETUP_URL, json={'status': 'completed'}).json() == {'status': 'completed'}
    assert google_setup_status(client.store.root) == {'status': 'completed'}
    assert client.get('/api/v1/vision/settings').json() == settings
    assert KEY not in (client.store.root / 'settings.json').read_text()


def test_setup_endpoints_require_session_auth(client):
    for url, body in [(TEST_URL, {'api_key': KEY}), (SETUP_URL, {'status': 'skipped'})]:
        response = client.post(url, json=body, headers={'Authorization': ''})
        assert response.status_code == 401 and KEY not in response.text
    assert client.get(SETUP_URL, headers={'Authorization': ''}).status_code == 401
