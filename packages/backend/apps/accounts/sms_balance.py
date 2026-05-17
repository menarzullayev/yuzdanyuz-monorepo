"""ISSUE-114 — SMS provider balance check (PlayMobile + Eskiz).

OTP-based auth SMS'siz ishlamaydi. Provider balansi tugasa OTP yuborilmaydi va
foydalanuvchilar kira olmaydi. Bu modul ikkala provider balansini API orqali oladi,
Prometheus `yz_sms_balance_uzs{backend}` gauge'ga yozadi va threshold pastida bo'lsa
`logger.error` chiqaradi (Grafana alert orqali on-call ogohlantiriladi).

API endpoints:
  * PlayMobile: GET https://send.smsgateway.uz/api/balance (Basic auth)
  * Eskiz:      GET https://notify.eskiz.uz/api/user/get-limit  (Bearer token)
"""

from __future__ import annotations

import base64
import logging
from typing import Any

import requests
from django.conf import settings

from core.metrics import SMS_BALANCE

logger = logging.getLogger(__name__)


def _threshold_uzs() -> int:
    return int(getattr(settings, 'SMS_BALANCE_THRESHOLD_UZS', 100000))


def check_playmobile_balance() -> dict[str, Any]:
    """PlayMobile balance ni UZS qaytaradi. Xato → None."""
    login = getattr(settings, 'PLAYMOBILE_LOGIN', '')
    password = getattr(settings, 'PLAYMOBILE_PASSWORD', '')
    if not (login and password):
        return {'backend': 'playmobile', 'balance_uzs': None, 'configured': False}

    credentials = base64.b64encode(f'{login}:{password}'.encode()).decode()
    try:
        resp = requests.get(
            'https://send.smsgateway.uz/api/balance',
            headers={'Authorization': f'Basic {credentials}'},
            timeout=10,
        )
        if resp.status_code != 200:
            logger.error('PlayMobile balance API %s: %s', resp.status_code, resp.text[:200])
            return {
                'backend': 'playmobile',
                'balance_uzs': None,
                'configured': True,
                'error': f'http_{resp.status_code}',
            }
        # API javob: {"balance": 12345.67} (UZS)
        data = resp.json()
        balance = int(float(data.get('balance', 0)))
        SMS_BALANCE.labels(backend='playmobile').set(balance)
        return {'backend': 'playmobile', 'balance_uzs': balance, 'configured': True}
    except (requests.RequestException, ValueError) as e:
        logger.error('PlayMobile balance check failed: %s', e)
        return {
            'backend': 'playmobile',
            'balance_uzs': None,
            'configured': True,
            'error': str(e)[:200],
        }


def check_eskiz_balance() -> dict[str, Any]:
    """Eskiz balance — qoldiq SMS soni emas, UZS qaytaradi.

    Eskiz auth: email/password → token, keyin token bilan `/api/user/get-limit`.
    Bu tasodifiy token kesh qilmaymiz (har soatda yangi, balance check kam chaqiriladi).
    """
    email = getattr(settings, 'ESKIZ_EMAIL', '')
    password = getattr(settings, 'ESKIZ_PASSWORD', '')
    if not (email and password):
        return {'backend': 'eskiz', 'balance_uzs': None, 'configured': False}

    try:
        auth_resp = requests.post(
            'https://notify.eskiz.uz/api/auth/login',
            data={'email': email, 'password': password},
            timeout=10,
        )
        token = auth_resp.json().get('data', {}).get('token')
        if not token:
            logger.error('Eskiz auth failed: %s', auth_resp.text[:200])
            return {
                'backend': 'eskiz',
                'balance_uzs': None,
                'configured': True,
                'error': 'auth_failed',
            }
        resp = requests.get(
            'https://notify.eskiz.uz/api/user/get-limit',
            headers={'Authorization': f'Bearer {token}'},
            timeout=10,
        )
        if resp.status_code != 200:
            logger.error('Eskiz balance %s: %s', resp.status_code, resp.text[:200])
            return {
                'backend': 'eskiz',
                'balance_uzs': None,
                'configured': True,
                'error': f'http_{resp.status_code}',
            }
        # API javob: {"data": {"balance": 12345}} (UZS)
        balance = int(resp.json().get('data', {}).get('balance', 0))
        SMS_BALANCE.labels(backend='eskiz').set(balance)
        return {'backend': 'eskiz', 'balance_uzs': balance, 'configured': True}
    except (requests.RequestException, ValueError, KeyError) as e:
        logger.error('Eskiz balance check failed: %s', e)
        return {
            'backend': 'eskiz',
            'balance_uzs': None,
            'configured': True,
            'error': str(e)[:200],
        }


def poll_sms_balances() -> dict[str, Any]:
    """Ikkala provider balansini tekshiradi. Threshold pastida log.error."""
    threshold = _threshold_uzs()
    results = {
        'playmobile': check_playmobile_balance(),
        'eskiz': check_eskiz_balance(),
    }
    low_providers = []
    for name, info in results.items():
        balance = info.get('balance_uzs')
        if not info.get('configured'):
            continue
        if balance is None:
            # Konfiguratsiya qilingan, lekin API javob bermadi — bu o'zi alert
            logger.error('SMS balance check returned None for %s — check API connectivity', name)
            continue
        if balance < threshold:
            low_providers.append(name)
            logger.error(
                'SMS balance below threshold: %s = %s UZS (threshold %s)',
                name,
                balance,
                threshold,
            )
    return {
        'threshold_uzs': threshold,
        'low': low_providers,
        'providers': results,
    }
