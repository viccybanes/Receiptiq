"""Vision options use synthetic data only; no real models or receipts."""

import copy
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.extraction import validate
from app.main import create_app
from app.models import ReceiptData
from app.vision import VisionEngine
from app.vision_settings import load_vision_settings


@pytest.fixture
def engine(tmp_path, monkeypatch):
    page = tmp_path / 'synthetic.png'
    Image.new('RGB', (800, 1000), 'white').save(page)
    state = {'items': [], 'requests': []}
    engine = VisionEngine()

    def request(body, stop):
        state['requests'].append(json.loads(body))
        data = state['replies'].pop(0) if state.get('replies') else state['data']
        return json.dumps({'done': True, 'message': {'content': json.dumps(data)}}).encode()

    monkeypatch.setattr(engine, '_request', request)
    return engine, page, state


@pytest.mark.parametrize(('unit', 'expected'), [('20', 'unit'), ('12.50', 'unit'), ('@', 'unit'),
    ('pieces', 'pcs'), ('MTRS.', 'mtrs'), ('box', 'boxes'), ('gallons', 'gals'),
    ('kilograms', 'kgs'), ('kilos', 'kilos'), ('m2', 'm2'), ('5 pcs', 'pcs'), ('20pcs', 'unit')])
def test_units_are_names_and_supported_aliases_normalize(engine, unit, expected):
    engine, page, state = engine
    state['data'] = {'visible_item_count': 1, 'items': [{'description': 'Material', 'quantity': '5', 'unit': unit}]}
    result = engine.extract([page], [])
    assert result['draft']['items'][0]['unit'] == expected
    draft = ReceiptData(items=[{'description': 'Material', 'unit': '20'}]).model_dump()
    assert any(issue['field'] == 'items.0.unit' and issue['blocking'] for issue in validate(draft))


@pytest.mark.parametrize(('count', 'boxes', 'expected'), [
    (1, None, 1), (2, None, 2), (None, None, 2),
    (2, [[0.1, 0.2, 0.9, 0.3]] * 2, 1),
    (2, [[0.1, 0.2, 0.9, 0.3], [0.1, 0.4, 0.9, 0.5]], 2),
    (1, [[0.1, 0.2, 0.9, 0.3], [0.1, 0.4, 0.9, 0.5]], 2),
])
def test_repeated_final_row_needs_count_or_same_source_to_remove(engine, count, boxes, expected):
    engine, page, state = engine
    items = [{'description': 'Cable', 'quantity': '2', 'unit': 'pcs', 'unit_price': '5', 'line_total': '10'} for _ in range(2)]
    if boxes:
        for item, box in zip(items, boxes):
            item.update(source_page=0, source_box=box)
    state['data'] = {'visible_item_count': count, 'items': items}
    result = engine.extract([page], [], capture_regions=bool(boxes))
    assert len(result['draft']['items']) == expected
    if expected == 1:
        assert 'Removed a repeated final item' in result['notice']
    else:
        assert 'two distinct receipt rows' in result['notice']


def test_rechecker_replaces_rows_without_appending_or_forcing_duplicates(engine):
    engine, page, state = engine
    state['replies'] = [
        {'visible_item_count': 2, 'items': [{'description': 'A'}]},
        {'visible_item_count': 2, 'items': [{'description': 'A'}, {'description': 'B'}, {'description': 'B'}]},
    ]
    result = engine.extract([page], [])
    assert len(state['requests']) == 2
    assert [item['description'] for item in result['draft']['items']] == ['A', 'B']
    assert result['row_audit']['mismatch'] is False


@pytest.mark.parametrize(('item', 'field', 'expected'), [
    ({'quantity': '', 'unit_price': '12.50', 'line_total': '50'}, 'quantity', '4'),
    ({'quantity': '4', 'unit_price': '', 'line_total': '50'}, 'unit_price', '12.50'),
    ({'quantity': '', 'unit_price': '3', 'line_total': '10'}, 'quantity', ''),
    ({'quantity': '3', 'unit_price': '', 'line_total': '10'}, 'unit_price', ''),
    ({'quantity': '4', 'unit_price': '12.50', 'line_total': '45'}, 'unit_price', '12.50'),
    ({'quantity': '', 'unit_price': '0', 'line_total': '50'}, 'quantity', ''),
])
def test_amount_first_is_optional_exact_and_preserves_discounts(engine, item, field, expected):
    engine, page, state = engine
    state['data'] = {'visible_item_count': 1, 'items': [{'description': 'Cable', **item}]}
    before = copy.deepcopy(state['data'])
    normal = engine.extract([page], [])
    assert normal['draft']['items'][0][field] == item[field]
    result = engine.extract([page], [], amount_first=True)
    assert result['draft']['items'][0][field] == expected
    assert state['data'] == before
    assert 'amount_first' in json.loads(state['requests'][-1]['messages'][1]['content'])
    if expected != item[field]:
        assert result['field_meta'][f'items.0.{field}']['source'] == 'derived'


def test_locations_are_optional_and_crop_coordinates_map_to_original(engine):
    engine, page, state = engine
    state['data'] = {'visible_item_count': 1, 'items': [
        {'description': 'Cable', 'source_page': 0, 'source_box': [0.1, 0.2, 0.9, 0.4]},
    ]}
    assert engine.extract([page], [])['source_regions'] == []
    assert 'source_box' not in state['requests'][-1]['format']['$defs']['VisionItem']['properties']
    result = engine.extract([page], [], capture_regions=True, crop=[0.2, 0.1, 0.8, 0.9])
    assert result['source_regions'][0]['box'] == pytest.approx([0.26, 0.26, 0.74, 0.42])
    assert result['source_regions'][0]['item_id'] == result['draft']['items'][0]['id']
    assert 'source_box' in state['requests'][-1]['format']['$defs']['VisionItem']['properties']


def test_numeric_unit_does_not_block_combined_quantity_and_amount_first_resolves_shift(engine, monkeypatch):
    engine, page, state = engine
    state['data'] = {'visible_item_count': 1, 'items': [
        {'description': 'Cable', 'quantity': '5 pcs', 'unit': '10.00', 'unit_price': '50.00', 'line_total': '50.00'},
    ]}
    result = engine.extract([page], [], amount_first=True)
    item = result['draft']['items'][0]
    assert (item['quantity'], item['unit'], item['unit_price'], item['line_total']) == ('5', 'pcs', '10.00', '50.00')
    assert result['field_meta']['items.0.unit_price']['source'] == 'derived'
    normal = engine.extract([page], [])
    item = normal['draft']['items'][0]
    assert (item['quantity'], item['unit'], item['unit_price']) == ('5', 'pcs', '10.00')
    assert normal['field_meta']['items.0.unit_price']['source'] == 'local_vision'
    assert 'recovered shifted Unit price' in normal['notice']
    monkeypatch.setenv('RECEIPTIQ_LAYOUT_ASSISTANCE', '0')
    assert engine.extract([page], [])['draft']['items'][0]['unit_price'] == '50.00'


def test_full_page_model_boxes_are_omitted_and_distinct_ocr_rows_supply_locations(engine):
    engine, page, state = engine
    state['data'] = {'visible_item_count': 2, 'items': [
        {'description': 'Bulb', 'quantity': '2', 'source_page': 0, 'source_box': [0, 0, 1, 1]},
        {'description': 'Bulb', 'quantity': '2', 'source_page': 0, 'source_box': [0, 0, 1, 1]},
    ]}
    assert engine.extract([page], [], capture_regions=True)['source_regions'] == []
    lines = [{'text': 'Bulb 2 pcs 10.00', 'page': 0, 'box': [80, y, 720, y + 30]} for y in (200, 300)]
    result = engine.extract([page], lines, capture_regions=True)
    assert len(result['draft']['items']) == 2 and len(result['source_regions']) == 2
    assert result['source_regions'][0]['box'] != result['source_regions'][1]['box']


def test_settings_persist_validate_and_keep_other_workspace_preferences(tmp_path):
    app = create_app(tmp_path, start_worker=False)
    with TestClient(app, base_url='http://127.0.0.1:8765') as client:
        client.headers['Authorization'] = 'Bearer ' + client.get('/api/v1/session').json()['token']
        (tmp_path / 'settings.json').write_text(json.dumps({'unrelated': {'keep': True}}))
        defaults = {'amount_first': False, 'capture_regions': False, 'provider': 'local',
                    'google_model': 'gemini-3.8-flash', 'google_consent': False, 'google_key_saved': False}
        assert client.get('/api/v1/vision/settings').json() == defaults
        assert client.patch('/api/v1/vision/settings', json={'amount_first': True}).status_code == 200
        result = client.patch('/api/v1/vision/settings', json={'capture_regions': True})
        assert result.json() == {**defaults, 'amount_first': True, 'capture_regions': True}
        assert load_vision_settings(tmp_path).amount_first is True
        assert json.loads((tmp_path / 'settings.json').read_text())['unrelated'] == {'keep': True}
        for invalid in ({'amount_first': 'false'}, {'capture_regions': 1}, {'unknown': True}):
            assert client.patch('/api/v1/vision/settings', json=invalid).status_code == 422


def test_worker_uses_saved_options_and_persists_derived_fields_and_page_locations(engine, tmp_path, monkeypatch):
    engine, page, state = engine
    app = create_app(tmp_path / 'workspace', start_worker=False)
    app.state.worker.vision = engine
    monkeypatch.setattr(app.state.worker.retrieval, 'search', lambda *_: [])
    state['data'] = {'visible_item_count': 1, 'items': [
        {'description': 'Cable', 'unit_price': '12.50', 'line_total': '50',
         'source_page': 0, 'source_box': [0.1, 0.2, 0.9, 0.3]},
    ]}
    with TestClient(app, base_url='http://127.0.0.1:8765') as client:
        client.headers['Authorization'] = 'Bearer ' + client.get('/api/v1/session').json()['token']
        client.patch('/api/v1/vision/settings', json={'amount_first': True, 'capture_regions': True})
        group = client.post('/api/v1/batches', json={'files': ['test.png']}).json()
        response = client.post('/api/v1/receipts',
                               params={'batch_id': group['id'], 'upload_id': group['files'][0]['id'], 'auto_ocr': False},
                               files={'file': ('test.png', page.read_bytes(), 'image/png')})
        assert response.status_code == 202
        assert app.state.worker.process_one() and app.state.worker.process_one()
        receipt = client.get('/api/v1/receipts/' + response.json()['id']).json()
        assert receipt['data']['items'][0]['quantity'] == '4'
        assert receipt['field_meta']['items.0.quantity']['source'] == 'derived'
        assert receipt['field_meta']['items.0.description']['page_id'] == receipt['pages'][0]['id']
        assert receipt['candidate']['source_regions'][0]['item_id'] == receipt['data']['items'][0]['id']
        assert receipt['candidate']['source_regions'][0]['page_id'] == receipt['pages'][0]['id']
