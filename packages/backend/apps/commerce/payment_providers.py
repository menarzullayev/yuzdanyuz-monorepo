"""
Payment provider abstraction — Payme/Click stub mode.

Production:
  Real Payme va Click SDK + merchant credentials .env'da.
  ENABLE_REAL_PAYMENTS=true bilan toggling.

Stub mode (default, dev/test):
  - initiate_charge() fake checkout URL qaytaradi
  - handle_webhook() simulyatsiya: provider_tx_id verify, signature stub
  - Real network call yo'q

Settings:
  PAYME_MERCHANT_ID         — production
  PAYME_SECRET_KEY          — production webhook signature verify
  CLICK_SERVICE_ID          — production
  CLICK_SECRET_KEY          — production
  ENABLE_REAL_PAYMENTS      — bool, default False (stub)
"""

import hashlib
import hmac
import logging
import secrets

from django.conf import settings

from .models import PaymentIntent

logger = logging.getLogger(__name__)


def _is_real_mode() -> bool:
    return getattr(settings, 'ENABLE_REAL_PAYMENTS', False)


# ── Provider interface ───────────────────────────────────────────────────────


class BaseProvider:
    name: str = ''

    def initiate_charge(self, intent: PaymentIntent) -> dict:
        """
        Returns: {checkout_url, provider_tx_id?, status: 'created'|'redirect'}
        Stub mode: fake checkout_url + auto-generated tx_id.
        """
        raise NotImplementedError

    def verify_webhook_signature(self, payload: dict, signature: str) -> bool:
        raise NotImplementedError

    def parse_webhook(self, payload: dict) -> dict:
        """
        Webhook payload'dan extract: {provider_tx_id, status, amount_uzs, error?}
        """
        raise NotImplementedError


# ── Payme stub ───────────────────────────────────────────────────────────────


class PaymeProvider(BaseProvider):
    name = 'payme'

    def initiate_charge(self, intent: PaymentIntent) -> dict:
        if _is_real_mode():
            # TODO: real Payme SDK integration
            raise NotImplementedError('Real Payme integratsiyasi kelajakda')

        # Stub: fake tx_id va checkout
        tx_id = f'payme-stub-{secrets.token_hex(8)}'
        intent.provider_tx_id = tx_id
        intent.status = PaymentIntent.Status.PROCESSING
        intent.save(update_fields=['provider_tx_id', 'status', 'updated_at'])
        return {
            'checkout_url': f'https://stub-payme.local/pay/{tx_id}',
            'provider_tx_id': tx_id,
            'status': 'created',
        }

    def verify_webhook_signature(self, payload: dict, signature: str) -> bool:
        if not _is_real_mode():
            # Stub: any non-empty signature accepted
            return bool(signature)
        secret = getattr(settings, 'PAYME_SECRET_KEY', '')
        if not secret:
            return False
        body = str(payload).encode()
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)

    def parse_webhook(self, payload: dict) -> dict:
        return {
            'provider_tx_id': payload.get('id') or payload.get('transaction'),
            'status': payload.get('state', 'succeeded'),  # Payme states map
            'amount_uzs': payload.get('amount', 0),
            'error': payload.get('error'),
        }


# ── Click stub ───────────────────────────────────────────────────────────────


class ClickProvider(BaseProvider):
    name = 'click'

    def initiate_charge(self, intent: PaymentIntent) -> dict:
        if _is_real_mode():
            raise NotImplementedError('Real Click integratsiyasi kelajakda')

        tx_id = f'click-stub-{secrets.token_hex(8)}'
        intent.provider_tx_id = tx_id
        intent.status = PaymentIntent.Status.PROCESSING
        intent.save(update_fields=['provider_tx_id', 'status', 'updated_at'])
        return {
            'checkout_url': f'https://stub-click.local/pay/{tx_id}',
            'provider_tx_id': tx_id,
            'status': 'created',
        }

    def verify_webhook_signature(self, payload: dict, signature: str) -> bool:
        if not _is_real_mode():
            return bool(signature)
        secret = getattr(settings, 'CLICK_SECRET_KEY', '')
        if not secret:
            return False
        body = str(payload).encode()
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)

    def parse_webhook(self, payload: dict) -> dict:
        return {
            'provider_tx_id': payload.get('payment_id'),
            'status': 'succeeded' if payload.get('status') == 1 else 'failed',
            'amount_uzs': payload.get('amount', 0),
            'error': payload.get('error_note'),
        }


# ── Provider registry ────────────────────────────────────────────────────────


_PROVIDERS = {
    'payme': PaymeProvider(),
    'click': ClickProvider(),
}


def get_provider(name: str) -> BaseProvider:
    if name not in _PROVIDERS:
        raise ValueError(f'Unknown provider: {name}')
    return _PROVIDERS[name]
