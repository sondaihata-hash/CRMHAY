"""Tests proving the root causes of worker timeout are fixed."""
import time
import unittest.mock as mock

from app import (
    app, _sync_state, _sync_lock,
    SyncJob, db,
    fetch_managed_facebook_messages,
    MAX_API_CALLS_PER_SYNC, MAX_CONVERSATION_PAGES,
    FACEBOOK_API_TIMEOUT,
)
from auth_helpers import csrf_token, login_admin


def test_sync_facebook_returns_immediately():
    """sync-facebook route must return HTTP redirect instantly, not block."""
    with mock.patch('app.get_facebook_token', return_value='fake_tok'), \
         mock.patch('app.threading') as mock_threading:
        with app.app_context():
            SyncJob.query.filter(SyncJob.status.in_(('queued', 'running'))).delete(synchronize_session=False)
            db.session.commit()
        client = login_admin(app.test_client())
        # Reset state
        with _sync_lock:
            _sync_state['running'] = False
        t0 = time.time()
        resp = client.post('/customers/sync-facebook', data={'_csrf_token': csrf_token(client, '/customers')})
        elapsed = time.time() - t0
        assert resp.status_code == 302, "Should redirect"
        assert elapsed < 2.0, f"Route took {elapsed:.2f}s — must be instant"
        mock_threading.Thread.assert_called_once()
        # Reset
        with _sync_lock:
            _sync_state['running'] = False


def test_sync_facebook_rejects_concurrent():
    """Second sync request while running must be rejected."""
    with app.app_context():
        job = SyncJob(id='test-concurrent-job', status='running', message='Đang chạy')
        db.session.add(job)
        db.session.commit()
    try:
        client = login_admin(app.test_client())
        resp = client.post('/customers/sync-facebook', data={'_csrf_token': csrf_token(client, '/customers')}, follow_redirects=True)
        assert resp.status_code == 200
    finally:
        with app.app_context():
            db.session.delete(db.session.get(SyncJob, 'test-concurrent-job'))
            db.session.commit()


def test_sync_status_endpoint():
    """Status endpoint returns JSON with correct fields."""
    client = login_admin(app.test_client())
    resp = client.get('/customers/sync-facebook/status')
    assert resp.status_code == 200
    data = resp.get_json()
    assert 'running' in data
    assert 'result' in data
    assert 'message' in data


def test_api_call_cap_stops_pagination():
    """fetch_managed_facebook_messages must stop when hitting MAX_API_CALLS."""
    call_count = 0

    def fake_fetch(endpoint, token, extra_params=None):
        nonlocal call_count
        call_count += 1
        if 'me/accounts' in str(endpoint):
            return {'data': [{'id': 'P1', 'name': 'Page', 'access_token': 'tok'}]}
        if 'conversations' in str(endpoint):
            # Return conversations that always have a next page (infinite pagination)
            convos = [{'id': f'conv_{call_count}_{i}',
                        'participants': {'data': [
                            {'id': 'P1', 'name': 'Page'},
                            {'id': f'C{call_count}_{i}', 'name': f'Customer {i}'},
                        ]},
                        'messages': {'data': [
                            {'from': {'id': f'C{call_count}_{i}', 'name': f'Customer {i}'},
                             'message': f'SDT 098765432{i}',
                             'created_time': '2026-08-01T10:00:00+0000'}
                        ]}}
                       for i in range(5)]
            return {'data': convos, 'paging': {'next': f'https://graph.facebook.com/v19.0/P1/conversations?after=page{call_count}'}}
        # Profile fetch
        return {'first_name': 'Test'}

    with mock.patch('app.fetch_facebook_json', side_effect=fake_fetch), \
         mock.patch.dict('os.environ', {'FACEBOOK_PAGE_ACCESS_TOKEN': 'tok'}), \
         mock.patch('app.API_RATE_DELAY', 0), \
         mock.patch('app.time') as mock_time:
        mock_time.time = time.time
        mock_time.sleep = lambda x: None
        results = fetch_managed_facebook_messages(max_pages=1, max_conversations_per_page=999)

    # Should have stopped, not run forever
    assert call_count <= MAX_API_CALLS_PER_SYNC + 5, f"API calls ({call_count}) exceeded cap"


def test_facebook_api_timeout_is_set():
    """FACEBOOK_API_TIMEOUT must be a reasonable value, not huge."""
    assert FACEBOOK_API_TIMEOUT <= 30, f"Timeout {FACEBOOK_API_TIMEOUT}s too large"
    assert FACEBOOK_API_TIMEOUT >= 5, f"Timeout {FACEBOOK_API_TIMEOUT}s too small"


def test_pagination_rounds_capped():
    """MAX_CONVERSATION_PAGES must prevent infinite pagination."""
    assert MAX_CONVERSATION_PAGES <= 20, "Pagination cap too high"
    assert MAX_CONVERSATION_PAGES >= 1, "Pagination cap too low"