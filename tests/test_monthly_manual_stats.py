from datetime import date

from app.routers import items
from tests.helpers import auth, default_fridge_id, signup


def test_monthly_totals_include_manual_purchases_and_discard_on_its_own_month(client, monkeypatch):
    user = signup(client, 'monthly')
    headers = auth(user)
    base = f'/fridges/{default_fridge_id(client, user)}'
    response = client.post(base + '/items', headers=headers, json={
        'name': '두부', 'quantity': 2, 'total_price': 5000, 'purchased_on': '2026-10-31'})
    assert response.status_code == 201
    monkeypatch.setattr(items, 'today', lambda: date(2026, 11, 1))
    assert client.post(base + f"/items/{response.json()['id']}/discard", headers=headers, json={'quantity': 1}).status_code == 200
    october = client.get(base + '/stats/monthly?year=2026&month=10', headers=headers).json()
    november = client.get(base + '/stats/monthly?year=2026&month=11', headers=headers).json()
    assert october['food_spent'] == 5000 and october['discarded_value'] == 0
    assert november['food_spent'] == 0 and november['discarded_value'] == 2500
    purchases = client.get(base + '/purchases?start=2026-10-01&end=2026-10-31', headers=headers).json()
    assert purchases[0]['total_price'] == 5000
