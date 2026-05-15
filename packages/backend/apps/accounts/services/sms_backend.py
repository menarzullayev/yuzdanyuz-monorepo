"""
SMS backend abstraktsiyasi.

Ishlatish:
    from apps.accounts.services.sms_backend import get_sms_backend
    backend = get_sms_backend()
    backend.send('+998901234567', 'Sizning kodingiz: 483920')

Backend settings.SMS_BACKEND orqali tanlanadi:
    'playmobile'  — PlayMobile (O'zbekiston, production)
    'console'     — dev uchun, SMS terminalga chiqariladi
    'dummy'       — test uchun, hech narsa qilmaydi
"""

import logging
from abc import ABC, abstractmethod

from django.conf import settings

log = logging.getLogger(__name__)


class SMSBackendBase(ABC):
    @abstractmethod
    def send(self, phone: str, text: str) -> bool:
        """SMS yuboradi. True = muvaffaqiyatli, False = xato."""


# ── Console backend (dev) ─────────────────────────────────────


class ConsoleSMSBackend(SMSBackendBase):
    def send(self, phone: str, text: str) -> bool:
        log.info('[SMS → %s] %s', phone, text)
        print(f'\n📱 SMS → {phone}\n{text}\n')
        return True


# ── Dummy backend (test) ──────────────────────────────────────


class DummySMSBackend(SMSBackendBase):
    def send(self, phone: str, text: str) -> bool:
        return True


# ── PlayMobile backend (production) ──────────────────────────


class PlayMobileSMSBackend(SMSBackendBase):
    """
    PlayMobile SMS Gateway.
    API: https://playmobile.uz/services/

    Credentials (.env):
        PLAYMOBILE_LOGIN
        PLAYMOBILE_PASSWORD
        PLAYMOBILE_ORIGINATOR  (max 11 belgi, masalan: "MilSert")
    """

    API_URL = 'https://send.smsgateway.uz/sms/broker'

    def send(self, phone: str, text: str) -> bool:
        import base64
        import uuid

        import requests as req

        login = getattr(settings, 'PLAYMOBILE_LOGIN', '')
        password = getattr(settings, 'PLAYMOBILE_PASSWORD', '')
        originator = getattr(settings, 'PLAYMOBILE_ORIGINATOR', 'MilSert')

        if not login or not password:
            log.error('PlayMobile credentials sozlanmagan (PLAYMOBILE_LOGIN/PASSWORD)')
            return False

        credentials = base64.b64encode(f'{login}:{password}'.encode()).decode()
        payload = {
            'messages': [
                {
                    'recipient': phone,
                    'message-id': str(uuid.uuid4()),
                    'sms': {
                        'originator': originator,
                        'content': {'text': text},
                    },
                }
            ]
        }
        try:
            resp = req.post(
                self.API_URL,
                json=payload,
                headers={'Authorization': f'Basic {credentials}'},
                timeout=10,
            )
            if resp.status_code == 200:
                return True
            log.error('PlayMobile xato: %s %s', resp.status_code, resp.text[:200])
            return False
        except req.RequestException as e:
            log.error('PlayMobile ulanish xatosi: %s', e)
            return False


# ── Eskiz backend (production, O'zbekiston) ───────────────────


class EskizSMSBackend(SMSBackendBase):
    """
    Eskiz.uz SMS Gateway — O'zbekistonda mashhur, arzonroq.
    API: https://eskiz.uz/api/auth/login

    Credentials (.env):
        ESKIZ_EMAIL
        ESKIZ_PASSWORD
    """

    AUTH_URL = 'https://notify.eskiz.uz/api/auth/login'
    SEND_URL = 'https://notify.eskiz.uz/api/message/sms/send'

    def _get_token(self) -> str | None:
        import requests as req

        try:
            resp = req.post(
                self.AUTH_URL,
                data={
                    'email': getattr(settings, 'ESKIZ_EMAIL', ''),
                    'password': getattr(settings, 'ESKIZ_PASSWORD', ''),
                },
                timeout=8,
            )
            data = resp.json()
            return data.get('data', {}).get('token')
        except Exception as e:
            log.error('Eskiz auth xatosi: %s', e)
            return None

    def send(self, phone: str, text: str) -> bool:
        import requests as req

        # E.164 → Eskiz 998XXXXXXXXX formatiga (+ belgisiz)
        normalized = phone.lstrip('+')

        token = self._get_token()
        if not token:
            return False

        from_whom = getattr(settings, 'ESKIZ_SENDER', '4546')
        try:
            resp = req.post(
                self.SEND_URL,
                data={
                    'mobile_phone': normalized,
                    'message': text,
                    'from': from_whom,
                },
                headers={'Authorization': f'Bearer {token}'},
                timeout=10,
            )
            result = resp.json()
            if result.get('status') == 'waiting':
                return True
            log.error('Eskiz xato: %s', result)
            return False
        except Exception as e:
            log.error('Eskiz yuborish xatosi: %s', e)
            return False


# ── Factory ───────────────────────────────────────────────────

_BACKENDS = {
    'playmobile': PlayMobileSMSBackend,
    'eskiz': EskizSMSBackend,
    'console': ConsoleSMSBackend,
    'dummy': DummySMSBackend,
}


def get_sms_backend() -> SMSBackendBase:
    name = getattr(settings, 'SMS_BACKEND', 'console')
    cls = _BACKENDS.get(name)
    if cls is None:
        raise ValueError(f'Noma\'lum SMS_BACKEND: "{name}". {list(_BACKENDS)} dan birini tanlang.')
    return cls()
