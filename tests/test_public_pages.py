import json
import unittest.mock as mock

from app import app


def test_public_commercial_pages_are_reachable():
    client = app.test_client()
    for path in ('/', '/privacy-policy', '/pricing', '/terms', '/data-deletion', '/healthz', '/readyz'):
        response = client.get(path)
        assert response.status_code in (200, 503)


def test_pricing_page_shows_launch_discount_and_doubled_limits():
    response = app.test_client().get('/pricing')
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert '149.500đ/tháng' in html
    assert '349.500đ/tháng' in html
    assert 'giảm 50%' in html
    assert 'Đến 20 tài khoản' in html
    assert '6 Facebook Page' in html


def test_homepage_shows_crmhay_contact_channels():
    html = app.test_client().get('/').get_data(as_text=True)
    assert '0707866676' in html
    assert '34/15 đường Trung Đông 7' in html
    assert 'zalo.me/0707866676' in html


def test_webhooks_fail_closed_without_secrets():
    client = app.test_client()
    with mock.patch.dict('os.environ', {}, clear=False):
        with mock.patch.dict('os.environ', {'FACEBOOK_APP_SECRET': ''}, clear=False):
            facebook = client.post('/api/facebook/webhook', data=json.dumps({'object': 'page'}), content_type='application/json')
        with mock.patch.dict('os.environ', {'ZALO_WEBHOOK_SECRET': ''}, clear=False):
            zalo = client.post('/api/zalo/webhook', data='{}', content_type='application/json')
    assert facebook.status_code == 403
    assert zalo.status_code == 503
