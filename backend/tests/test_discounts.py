"""Explicit percentage discounts preserve gross inputs, human overrides, and the CSV contract."""

import copy
import csv
import io
import zipfile
from decimal import Decimal

import pytest
from test_workflow import client as client_fixture
from test_workflow import ready_receipt, verify

from app.backups import apply_staged_restore, create_backup, stage_restore
from app.db import Store
from app.extraction import discounted_amount, discounted_price, reconcile, validate
from app.service import COLUMNS
from app.vision import VisionReceipt, _derive_from_amount, _merge

client = client_fixture


def save(client, receipt, data):
    response = client.patch('/api/v1/receipts/' + receipt['id'], json={'revision': receipt['revision'], 'data': data})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize('quantity,price,percent,net_price,amount', [
    ('2', '100', '10', '90', '180.00'),
    ('3', '0.05', '10', '0.045', '0.14'),
    ('1', '0.01', '50', '0.005', '0.01'),
    ('2', '0.99', '12.5', '0.86625', '1.73'),
    ('1', '100', '100', '0', '0.00'),
    ('-2', '100', '10', '90', '-180.00'),
    ('-2', '100', '100', '0', '0.00'),
    ('2', '100', '0', '100', '200.00'),
    ('3', '1.234567890123', '0.123456', '1.233043741989', '3.70'),
])
def test_percentage_rounding(quantity, price, percent, net_price, amount):
    item = {'quantity': quantity, 'unit_price': price}
    assert discounted_price(price, percent) == Decimal(net_price)
    assert str(discounted_amount(item, {'total_discount_percent': percent})) == amount


def test_defaults_overrides_zero_clear_and_reload(client):
    receipt, _ = ready_receipt(client)
    data = copy.deepcopy(receipt['data'])
    assert data['total_discount_percent'] == '' and all(item['discount_percent'] == '' for item in data['items'])
    data['items'][1]['discount_percent'] = '20'
    receipt = save(client, receipt, data)
    assert receipt['data']['total'] == '120.00'
    for percent, amounts, total in [('10', ['90.00', '20.00'], '110.00'), ('20', ['80.00', '20.00'], '100.00')]:
        data = copy.deepcopy(receipt['data'])
        data['total_discount_percent'] = percent
        receipt = save(client, receipt, data)
        assert [item['line_total'] for item in receipt['data']['items']] == amounts
        assert receipt['data']['total'] == total
        assert [item['unit_price'] for item in receipt['data']['items']] == ['50.00', '25.00']
    data = copy.deepcopy(receipt['data'])
    data['items'][1]['discount_percent'] = '0'
    receipt = save(client, receipt, data)
    assert receipt['data']['total'] == '105.00'
    data = copy.deepcopy(receipt['data'])
    data['items'].reverse()
    data['total_discount_percent'] = '50'
    receipt = save(client, receipt, data)
    assert [item['line_total'] for item in receipt['data']['items']] == ['25.00', '50.00']
    data = copy.deepcopy(receipt['data'])
    data['items'][0]['discount_percent'] = ''
    receipt = save(client, receipt, data)
    assert receipt['data']['total'] == '62.50'
    data = copy.deepcopy(receipt['data'])
    data['total_discount_percent'] = ''
    receipt = save(client, receipt, data)
    assert receipt['data']['total'] == '125.00'
    assert Store(client.store.root).receipt(receipt['id'])['data'] == receipt['data']


@pytest.mark.parametrize('field', ['total', 'line'])
@pytest.mark.parametrize('value', ['-1', '100.01', 'NaN', '1e1', '10%', '1.1234567'])
def test_invalid_percentages_are_editable_but_block_verification(client, field, value):
    receipt, _ = ready_receipt(client)
    data = copy.deepcopy(receipt['data'])
    if field == 'total':
        data['total_discount_percent'] = value
        issue_field = 'total_discount_percent'
    else:
        data['items'][0]['discount_percent'] = value
        issue_field = 'items.0.discount_percent'
    receipt = save(client, receipt, data)
    assert any(issue['field'] == issue_field and issue['blocking'] for issue in receipt['issues'])
    assert verify(client, receipt).status_code == 422


def test_discounted_exports_keep_columns_and_backup_roundtrip(client, tmp_path):
    receipt, _ = ready_receipt(client)
    data = copy.deepcopy(receipt['data'])
    data['total_discount_percent'] = '10'
    data['items'][1]['discount_percent'] = '20'
    receipt = save(client, receipt, data)
    assert verify(client, receipt).status_code == 200
    selected = {'receipt_ids': [receipt['id']]}
    for photos in (False, True):
        exported = client.post('/api/v1/exports', json={**selected, 'include_photos': photos})
        assert exported.status_code == 201, exported.text
        content = client.get('/api/v1/exports/' + exported.json()['id'] + '/download').content
        if photos:
            with zipfile.ZipFile(io.BytesIO(content)) as bundle:
                content = bundle.read('receipts.csv')
        reader = csv.DictReader(io.StringIO(content.decode('utf-8-sig')))
        assert reader.fieldnames == COLUMNS
        rows = list(reader)
        assert [row['Unit price'] for row in rows] == ['45', '20']
        assert [row['Amount'] for row in rows] == ['90.00', '20.00']
        assert all(row['Total'] == '110.00' and row['Notes'] == data['notes'] for row in rows)
        assert all((Decimal(row['Quantity']) * Decimal(row['Unit price'])).quantize(Decimal('0.01')) == Decimal(row['Amount'])
                   for row in rows)
    archive = create_backup(client.store)
    restored_root = tmp_path / 'restored'
    Store(restored_root)
    stage_restore(restored_root, archive)
    apply_staged_restore(restored_root)
    assert Store(restored_root).receipt(receipt['id'])['data'] == receipt['data']
    # A later manual Amount conflict cannot export a price that disagrees with the net Amount.
    data = copy.deepcopy(receipt['data'])
    data['items'][0]['line_total'] = '99.00'
    receipt = save(client, receipt, data)
    assert receipt['verified_revision'] is None
    assert any(issue['field'] == 'items.0.arithmetic' and issue['blocking'] for issue in receipt['issues'])
    assert verify(client, receipt).status_code == 422
    assert client.post('/api/v1/exports', json=selected).status_code == 409


def test_new_lines_inherit_and_vision_preserves_manual_percentages(client):
    receipt, _ = ready_receipt(client)
    data = copy.deepcopy(receipt['data'])
    data['total_discount_percent'] = '10'
    data['items'][0]['discount_percent'] = '0'
    receipt = save(client, receipt, data)
    data = copy.deepcopy(receipt['data'])
    data['items'].append({'description': 'New line', 'quantity': '2', 'unit_price': '10', 'unit': 'pcs'})
    receipt = save(client, receipt, data)
    assert receipt['data']['items'][-1]['line_total'] == '18.00'
    assert receipt['data']['total'] == '140.50'
    assert not reconcile(receipt['data'])['can_allocate']
    candidate = VisionReceipt.model_validate({'items': [{'description': 'Coffee', 'quantity': '2', 'unit_price': '50', 'line_total': '100'}]})
    draft, _ = _merge(candidate, receipt['data'], None, preserve_items=True)
    assert draft['total_discount_percent'] == '10' and draft['items'][0]['discount_percent'] == '0'
    draft['items'][0].update(quantity='', unit_price='50', line_total='90')
    assert _derive_from_amount(draft) == [] and draft['items'][0]['quantity'] == ''
    assert not any(issue['field'] == 'items.2.arithmetic' for issue in validate(receipt['data']))


def test_older_client_omission_does_not_clear_saved_discounts(client):
    receipt, _ = ready_receipt(client)
    data = copy.deepcopy(receipt['data'])
    data['total_discount_percent'] = '10'
    data['items'][1]['discount_percent'] = '0'
    receipt = save(client, receipt, data)
    old_client_data = copy.deepcopy(receipt['data'])
    del old_client_data['total_discount_percent']
    for item in old_client_data['items']:
        del item['discount_percent']
    old_client_data['notes'] = 'Older client changed Notes'
    receipt = save(client, receipt, old_client_data)
    assert receipt['data']['total_discount_percent'] == '10'
    assert [item['discount_percent'] for item in receipt['data']['items']] == ['', '0']
    assert receipt['data']['total'] == '115.00'
