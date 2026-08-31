#!/usr/bin/env python3
"""Local sales-machine helper for the PA1 workflow.

This does not use Zalo OA/API. It is meant to run on the same machine where a sales
representative uses their personal Zalo account. The agent simply forwards messages
and replies between the local Zalo UI and the CRM web app.

Typical usage:
  python zalo_local_agent.py --crm-url http://localhost:5000 --mode sync --customer-phone 0909000001 --name "Nguyen Van A" --message "Xin chao"
  python zalo_local_agent.py --crm-url http://localhost:5000 --mode reply --customer-id 12 --message "Cam on ban"
"""

import argparse
import json
from urllib import request, error


def api_call(url, payload=None, method='POST'):
    data = None if payload is None else json.dumps(payload).encode('utf-8')
    req = request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header('Content-Type', 'application/json')
    try:
        with request.urlopen(req, timeout=20) as response:
            body = response.read().decode('utf-8')
            return response.status, body
    except error.HTTPError as exc:
        body = exc.read().decode('utf-8', errors='ignore')
        return exc.code, body


def sync_message(crm_url, customer_phone, sender_id, name, message, sent_at=None):
    payload = {
        'customer_phone': customer_phone,
        'sender_id': sender_id,
        'name': name,
        'message': message,
        'sent_at': sent_at,
        'channel': 'zalo_personal',
        'source': 'local_agent',
    }
    return api_call(f'{crm_url.rstrip("/")}/api/zalo/webhook', payload)


def send_reply(crm_url, customer_id, message):
    form_data = {'zalo_message': message}
    from urllib.parse import urlencode
    encoded = urlencode(form_data).encode('utf-8')
    req = request.Request(f'{crm_url.rstrip("/")}/customers/{customer_id}/send-zalo', data=encoded, method='POST')
    req.add_header('Content-Type', 'application/x-www-form-urlencoded')
    try:
        with request.urlopen(req, timeout=20) as response:
            return response.status, response.read().decode('utf-8', errors='ignore')
    except error.HTTPError as exc:
        body = exc.read().decode('utf-8', errors='ignore')
        return exc.code, body


def main():
    parser = argparse.ArgumentParser(description='Local Zalo personal agent for CRM sync')
    parser.add_argument('--crm-url', required=True, help='CRM base URL, e.g. http://localhost:5000')
    parser.add_argument('--mode', choices=['sync', 'reply'], required=True)
    parser.add_argument('--customer-phone', default='')
    parser.add_argument('--customer-id', default='')
    parser.add_argument('--sender-id', default='zalo-local-agent')
    parser.add_argument('--name', default='Khách hàng Zalo')
    parser.add_argument('--message', default='')
    parser.add_argument('--sent-at', default='')
    args = parser.parse_args()

    if args.mode == 'sync':
        status, body = sync_message(
            args.crm_url,
            args.customer_phone,
            args.sender_id,
            args.name,
            args.message,
            args.sent_at,
        )
        print(f'status={status}')
        print(body)
        return

    if args.mode == 'reply':
        status, body = send_reply(args.crm_url, int(args.customer_id), args.message)
        print(f'status={status}')
        print(body)
        return


if __name__ == '__main__':
    main()
