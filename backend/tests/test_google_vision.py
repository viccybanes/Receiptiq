"""Synthetic Gemini transport and real workspace lifecycle checks; never calls Google."""

import asyncio
import copy
import json
import os
import time
import zipfile

import httpx
import pytest
from pydantic import ValidationError
from test_workflow import batch, ready_receipt, upload, verify
from test_workflow import client as client_fixture

from app import google_vision
from app.backups import create_backup
from app.google_key import KEY_FILE, load_google_key
from app.system import _location_files
from app.vision import VisionError, VisionReceipt, _output_schema
from app.vision_settings import VisionSettings, load_vision_settings, save_vision_settings

client = client_fixture
KEY = "AQ.synthetic-google-key-never-a-real-credential"
MODEL = "gemini-3.8-flash"
RECEIPT = {"supplier": "GOOGLE SAMPLE", "receipt_number": "0001 / 0002", "receipt_date": "2026-10-28",
           "currency": "PHP", "total": "100.00", "visible_item_count": 1,
           "items": [{"description": "Cable", "quantity": "2", "unit": "pcs", "unit_price": "50.00", "line_total": "100.00"}]}


@pytest.fixture
def google(client, monkeypatch):
    # Exercise DPAPI on Windows; other platforms can still run transport/lifecycle checks without a plaintext fallback.
    if os.name != "nt":
        monkeypatch.setattr("app.google_key._protect", lambda value, **_: value[::-1])
    assert client.put('/api/v1/vision/google-key', json={'api_key': KEY}).status_code == 200
    enabled = client.patch('/api/v1/vision/settings', json={'provider': 'google', 'google_consent': True})
    assert enabled.status_code == 200, enabled.text
    monkeypatch.setattr(client.worker.retrieval, 'search', lambda *args, **kwargs: [])
    state = {'requests': [], 'result': copy.deepcopy(RECEIPT), 'status': 200}

    async def handler(request):
        state['requests'].append(request)
        if state.get('on_request'):
            state['on_request']()
        if state.get('delay'):
            await asyncio.sleep(state['delay'])
        if state.get('exception'):
            raise state['exception']
        if state['status'] != 200:
            return httpx.Response(state['status'], json={'error': {'message': KEY + ' private provider error'}},
                                  headers={'Location': 'https://example.invalid/steal-key'})
        content = state.get('content', json.dumps(state['result']))
        reply = {'candidates': [{'finishReason': state.get('finish', 'STOP'),
                                'content': {'parts': [{'text': content}]}}]}
        return httpx.Response(200, json=state.get('reply', reply))

    original_client = httpx.AsyncClient

    def mock_client(**kwargs):
        assert kwargs['trust_env'] is False and kwargs['follow_redirects'] is False
        return original_client(**kwargs, transport=httpx.MockTransport(handler))

    monkeypatch.setattr(google_vision.httpx, 'AsyncClient', mock_client)
    state['engine'] = google_vision.GoogleVisionEngine(client.store.root)
    return state


def scan(client, receipt):
    result = client.post(f"/api/v1/receipts/{receipt['id']}/vision", json={
        'revision': receipt['revision'], 'page_id': receipt['pages'][0]['id'], 'crop': [0, 0, 1, 1]})
    assert result.status_code == 202, result.text
    return result.json()['id']


@pytest.mark.parametrize('key', [KEY, 'AIza' + 'synthetic-key-never-a-real-credential'])
def test_google_key_formats_save_and_round_trip(client, monkeypatch, key):
    if os.name != 'nt':
        monkeypatch.setattr('app.google_key._protect', lambda value, **_: value[::-1])
    saved = client.put('/api/v1/vision/google-key', json={'api_key': ' ' + key + ' '})
    assert saved.status_code == 200, saved.text
    assert saved.json()['google_key_saved'] is True
    assert saved.json()['provider'] == 'local' and saved.json()['google_consent'] is False
    assert load_google_key(client.store.root) == key
    assert key.encode() not in (client.store.root / KEY_FILE).read_bytes()
    assert key not in saved.text


@pytest.mark.parametrize('invalid_key', ['', 'short', 'a' * 257, KEY + '\r\nsecret',
                                       KEY + '\tsecret', KEY + ' secret', KEY + '\x00', KEY + '\u00e9'])
def test_invalid_key_preserves_saved_key_and_is_not_echoed(client, google, invalid_key):
    encrypted = (client.store.root / KEY_FILE).read_bytes()
    result = client.put('/api/v1/vision/google-key', json={'api_key': invalid_key})
    assert result.status_code == 422
    assert result.json()['detail'] == 'Enter a valid Google AI Studio API key.'
    assert (client.store.root / KEY_FILE).read_bytes() == encrypted
    assert load_google_key(client.store.root) == KEY


def test_key_is_write_only_protected_and_excluded_from_backups(client, google):
    root = client.store.root
    assert load_google_key(root) == KEY
    assert KEY.encode() not in (root / KEY_FILE).read_bytes()
    assert KEY not in (root / 'settings.json').read_text()
    assert KEY not in client.get('/api/v1/vision/settings').text
    assert client.get('/api/v1/vision/settings').json()['google_key_saved'] is True
    assert root / KEY_FILE in _location_files(root)
    with zipfile.ZipFile(create_backup(client.store)) as archive:
        assert KEY_FILE not in archive.namelist()
        assert all(KEY.encode() not in archive.read(name) for name in archive.namelist())
    removed = client.delete('/api/v1/vision/google-key').json()
    assert removed['provider'] == 'local' and removed['google_consent'] is False
    assert removed['google_key_saved'] is False and not (root / KEY_FILE).exists()


def test_google_requires_key_and_explicit_consent_and_sanitizes_invalid_key(client, monkeypatch):
    if os.name != 'nt':
        monkeypatch.setattr('app.google_key._protect', lambda value, **_: value[::-1])
    assert load_vision_settings(client.store.root).provider == 'local'
    assert client.patch('/api/v1/vision/settings', json={'provider': 'google', 'google_consent': True}).status_code == 422
    for invalid in ({'api_key': KEY + '\r\nsecret'}, {'api_key': [KEY]}, {'api_key': KEY, 'extra': KEY}):
        result = client.put('/api/v1/vision/google-key', json=invalid)
        assert result.status_code == 422 and KEY not in result.text
    assert client.put('/api/v1/vision/google-key', json={'api_key': KEY}).status_code == 200
    assert load_vision_settings(client.store.root).provider == 'local'
    assert client.patch('/api/v1/vision/settings', json={'provider': 'google'}).status_code == 422
    assert client.patch('/api/v1/vision/settings', json={'google_model': '../../evil'}).status_code == 422
    assert client.patch('/api/v1/vision/settings', json={'google_consent': 'true'}).status_code == 422
    assert client.put('/api/v1/vision/google-key', json={'api_key': KEY}, headers={'Authorization': 'wrong'}).status_code == 401


def test_reentering_key_after_restore_requires_enabling_google_again(client, google):
    # Backups retain settings but intentionally omit credentials.
    (client.store.root / KEY_FILE).unlink()
    restored = client.get('/api/v1/vision/settings').json()
    assert restored['provider'] == 'google' and restored['google_key_saved'] is False
    saved = client.put('/api/v1/vision/google-key', json={'api_key': KEY}).json()
    assert saved['google_key_saved'] is True
    assert saved['provider'] == 'local' and saved['google_consent'] is False


@pytest.mark.parametrize('provider, model', [('google', MODEL), ('google', 'gemini-3.5-flash-lite'), ('local', 'qwen3-vl:2b-instruct'),
                                           ('local', 'qwen3-vl:4b-instruct')])
def test_manual_model_choice_routes_without_changing_upload_provider(client, google, monkeypatch, provider, model):
    receipt, _ = ready_receipt(client)
    local_models = []

    def local_extract(images, lines, **options):
        local_models.append(options['model'])
        return {'draft': options['current'], 'model': options['model'], 'field_meta': {}}

    monkeypatch.setattr(client.worker.vision, 'extract', local_extract)
    body = {'revision': receipt['revision'], 'page_id': receipt['pages'][0]['id'], 'provider': provider}
    if provider == 'local':
        body['model'] = model
    elif model != MODEL:
        body['google_model'] = model
    response = client.post(f"/api/v1/receipts/{receipt['id']}/vision", json=body)
    assert response.status_code == 202, response.text
    job = client.store.one('SELECT payload_json FROM jobs WHERE id=?', (response.json()['id'],))
    payload = json.loads(job['payload_json'])
    assert payload['provider'] == provider and payload['model'] == model
    assert client.worker.process_one()
    assert local_models == ([model] if provider == 'local' else [])
    assert len(google['requests']) == (1 if provider == 'google' else 0)
    if provider == 'google':
        assert google['requests'][0].url.path.endswith(f'/models/{model}:generateContent')
    assert load_vision_settings(client.store.root).provider == 'google'
    assert load_vision_settings(client.store.root).google_model == MODEL


@pytest.mark.parametrize('unavailable', ['disabled', 'missing-key'])
def test_manual_google_choice_requires_enabled_provider_and_key(client, google, unavailable):
    receipt, _ = ready_receipt(client)
    if unavailable == 'disabled':
        client.patch('/api/v1/vision/settings', json={'provider': 'local'})
    else:
        (client.store.root / KEY_FILE).unlink()
    response = client.post(f"/api/v1/receipts/{receipt['id']}/vision", json={
        'revision': receipt['revision'], 'page_id': receipt['pages'][0]['id'], 'provider': 'google',
        'google_model': 'gemini-3.5-flash-lite'})
    assert response.status_code == 409 and 'disabled' in response.json()['detail']
    assert not google['requests']
    assert not client.store.one("SELECT id FROM jobs WHERE receipt_id=? AND kind='vision'", (receipt['id'],))


@pytest.mark.parametrize('capture_regions', [False, True])
def test_google_schema_omits_large_array_bound_but_retains_local_validation(client, google, capture_regions):
    schema = _output_schema(capture_regions)
    original = copy.deepcopy(schema)
    google['engine']._read_receipt({'model': MODEL, 'format': schema, 'messages': [
        {'content': 'Read the synthetic receipt.'}, {'content': '{}', 'images': []}]}, stop=None)
    sent = json.loads(google['requests'][0].content)['generationConfig']['responseJsonSchema']
    assert 'maxItems' not in sent['properties']['items']
    assert sent['properties']['reading_notes']['maxItems'] == 12
    if capture_regions:
        assert sent['$defs']['VisionItem']['properties']['source_box']['anyOf'][0]['maxItems'] == 4
    assert schema == original and schema['properties']['items']['maxItems'] == 1000
    with pytest.raises(ValidationError):
        VisionReceipt.model_validate({'items': [{}] * 1001})


def test_manual_google_model_rejects_invalid_id(client, google):
    receipt, _ = ready_receipt(client)
    response = client.post(f"/api/v1/receipts/{receipt['id']}/vision", json={
        'revision': receipt['revision'], 'page_id': receipt['pages'][0]['id'], 'provider': 'google',
        'google_model': '../../other-host'})
    assert response.status_code == 422 and not google['requests']


def test_google_manual_scan_preserves_verification_until_apply_and_sends_no_history(client, google, monkeypatch):
    receipt, _ = ready_receipt(client)
    assert verify(client, receipt).status_code == 200
    monkeypatch.setattr(client.worker.retrieval, 'search', lambda *_: pytest.fail('Cloud scan requested historical examples'))
    job_id = scan(client, receipt)
    # A later model setting must not change an accepted job's model.
    client.patch('/api/v1/vision/settings', json={'google_model': 'gemini-other-model'})
    assert client.worker.process_one()
    current = client.get('/api/v1/receipts/' + receipt['id']).json()
    candidate = current['candidate']
    assert candidate and candidate['model'] == MODEL
    assert candidate['provenance']['source'] == 'google_vision'
    assert candidate['provenance']['layout_assistance'] is False
    assert candidate['examples'] == [] and candidate['provenance']['historical_examples'] == 0
    assert current['status'] == 'verified' and current['data'] == receipt['data']
    assert current['evidence']['lines'] == receipt['evidence']['lines']
    request = google['requests'][0]
    assert len(google['requests']) == 1
    assert request.url.host == 'generativelanguage.googleapis.com' and request.url.scheme == 'https'
    assert request.url.path.endswith(f'/models/{MODEL}:generateContent') and not request.url.query
    assert request.headers['x-goog-api-key'] == KEY
    body = json.loads(request.content)
    assert body['contents'][0]['parts'][1]['inlineData']['mimeType'] == 'image/jpeg'
    assert json.loads(body['contents'][0]['parts'][0]['text'])['historical_examples'] == []
    assert body['generationConfig']['responseMimeType'] == 'application/json'
    assert KEY not in request.content.decode() and KEY not in json.dumps(current)
    job = client.store.one('SELECT * FROM jobs WHERE id=?', (job_id,))
    assert KEY not in json.dumps(job)
    applied = client.post(f"/api/v1/receipts/{receipt['id']}/candidates/{candidate['id']}/apply",
                          json={'revision': receipt['revision']})
    assert applied.status_code == 200, applied.text
    assert applied.json()['status'] == 'needs_review'
    assert applied.json()['data']['supplier'] == 'GOOGLE SAMPLE'
    assert applied.json()['data']['receipt_number'] == '0001 / 0002'


@pytest.mark.parametrize('edit_during_scan', [False, True])
def test_google_upload_auto_apply_preserves_intervening_edits(client, google, edit_during_scan):
    accepted = upload(client, batch(client), auto_ocr=False).json()
    assert client.worker.process_one()  # Prepare preview and queue the pinned provider.
    receipt = client.store.receipt(accepted['id'])

    def edit():
        receipt['data']['supplier'] = 'HUMAN CORRECTION'
        response = client.patch('/api/v1/receipts/' + receipt['id'], json={
            'revision': receipt['revision'], 'data': receipt['data']})
        assert response.status_code == 200

    if edit_during_scan:
        google['on_request'] = edit
    assert client.worker.process_one()
    current = client.store.receipt(receipt['id'])
    assert current['status'] == 'needs_review' and current['verified_revision'] is None
    assert current['data']['supplier'] == ('HUMAN CORRECTION' if edit_during_scan else 'GOOGLE SAMPLE')
    assert bool(current['candidate'].get('auto_applied')) is not edit_during_scan
    if edit_during_scan:
        assert client.post(f"/api/v1/receipts/{receipt['id']}/candidates/{current['candidate']['id']}/apply",
                           json={'revision': current['revision']}).status_code == 409


@pytest.mark.parametrize('legacy', [False, True])
def test_local_upload_never_switches_to_cloud_after_acceptance(client, google, monkeypatch, legacy):
    client.patch('/api/v1/vision/settings', json={'provider': 'local'})
    accepted = upload(client, batch(client), auto_ocr=False).json()
    if legacy:
        client.store.execute('UPDATE jobs SET payload_json=? WHERE id=?',
                             (json.dumps({'auto_vision': True}), accepted['job_id']))
    client.patch('/api/v1/vision/settings', json={'provider': 'google', 'google_consent': True})
    local_calls = []

    def local_extract(images, lines, **options):
        local_calls.append(options)
        return {'draft': options['current'], 'model': options['model'], 'field_meta': {}}

    monkeypatch.setattr(client.worker.vision, 'extract', local_extract)
    assert client.worker.process_one() and client.worker.process_one()
    assert len(local_calls) == 1 and local_calls[0]['model'].startswith('qwen')
    assert not google['requests']


def test_revoked_google_upload_is_not_sent_or_retried(client, google):
    accepted = upload(client, batch(client), auto_ocr=False).json()
    client.patch('/api/v1/vision/settings', json={'provider': 'local'})
    assert client.worker.process_one() and client.worker.process_one()
    job = client.store.receipt(accepted['id'])['vision_job']
    assert job['state'] == 'failed' and 'disabled' in job['error']
    assert not client.worker.process_one() and not google['requests']


@pytest.mark.parametrize('status, hint', [(400, 'rejected'), (401, 'key'), (403, 'denied'), (404, 'model'),
                                       (429, 'quota'), (500, 'unavailable'), (503, 'busy'), (302, 'unavailable')])
def test_cloud_http_errors_are_safe_visible_and_not_automatically_retried(client, google, status, hint, caplog):
    receipt, _ = ready_receipt(client)
    google['status'] = status
    scan(client, receipt)
    assert client.worker.process_one()
    job = client.store.receipt(receipt['id'])['vision_job']
    assert job['state'] == 'failed' and hint in job['error'].lower()
    assert KEY not in job['error'] and KEY not in caplog.text
    assert not client.worker.process_one() and len(google['requests']) == 1


@pytest.mark.parametrize('mode', ['cancel', 'revoke'])
def test_cloud_wait_is_cancellable_and_rechecks_consent(client, google, mode):
    receipt, _ = ready_receipt(client)
    path = client.store.file(client.store.one('SELECT processed_path FROM document_pages WHERE id=?',
                                             (receipt['pages'][0]['id'],))['processed_path'])
    google['delay'] = 3
    if mode == 'revoke':
        google['on_request'] = lambda: save_vision_settings(client.store.root, VisionSettings(provider='local'))
    started = time.monotonic()
    with pytest.raises((VisionError, ValueError), match='cancelled|disabled'):
        google['engine'].extract([path], [], model=MODEL,
                                 stop=lambda: mode == 'cancel' and time.monotonic() - started > 0.1)
    assert time.monotonic() - started < 2


@pytest.mark.parametrize('finish, content, partial', [
    ('MAX_TOKENS', '{"supplier":"PARTIAL","items":[{"description":"Cable"', True),
    ('STOP', 'not receipt JSON', False),
    ('SAFETY', '{}', False),
])
def test_cloud_recovery_and_blocked_output_are_not_silently_accepted(client, google, finish, content, partial):
    receipt, _ = ready_receipt(client)
    google.update(finish=finish, content=content)
    scan(client, receipt)
    assert client.worker.process_one()
    current = client.store.receipt(receipt['id'])
    if partial:
        assert current['candidate']['provenance']['partial_response'] is True
        assert 'Recovered partial' in current['candidate']['notice']
    else:
        assert current['vision_job']['state'] == 'failed' and current['candidate'] is None


@pytest.mark.parametrize('exception, hint', [(httpx.ReadTimeout, 'response'), (httpx.ConnectTimeout, 'Connection')])
def test_cloud_timeout_error_hides_transport_details(client, google, caplog, exception, hint):
    receipt, _ = ready_receipt(client)
    google['exception'] = exception(KEY)
    scan(client, receipt)
    assert client.worker.process_one()
    job = client.store.receipt(receipt['id'])['vision_job']
    assert 'timed out' in job['error'] and hint in job['error'] and KEY not in job['error'] and KEY not in caplog.text


def test_cloud_response_limit(client, google, monkeypatch):
    receipt, _ = ready_receipt(client)
    monkeypatch.setattr(google_vision, 'MAX_RESPONSE_BYTES', 50)
    scan(client, receipt)
    assert client.worker.process_one()
    job = client.store.receipt(receipt['id'])['vision_job']
    assert job['state'] == 'failed' and 'oversized' in job['error']
