"""
Telegram Bot API orqali xabar yuborish.
python-telegram-bot kutubxonasiz — to'g'ridan-to'g'ri HTTP (sodda va ishonchli).
"""

import logging

import requests
from django.conf import settings

log = logging.getLogger(__name__)

TELEGRAM_API = 'https://api.telegram.org/bot{token}/sendMessage'


def send_message(telegram_id: int, text: str) -> bool:
    """Bot orqali Telegram foydalanuvchiga xabar yuboradi."""
    token = getattr(settings, 'TELEGRAM_BOT_TOKEN', '')
    if not token:
        log.error('TELEGRAM_BOT_TOKEN sozlanmagan')
        return False

    try:
        resp = requests.post(
            TELEGRAM_API.format(token=token),
            json={
                'chat_id': telegram_id,
                'text': text,
                'parse_mode': 'HTML',
            },
            timeout=8,
        )
        if resp.status_code == 200 and resp.json().get('ok'):
            return True
        log.error('Telegram sendMessage xato: %s', resp.text[:200])
        return False
    except requests.RequestException as e:
        log.error('Telegram API ulanish xatosi: %s', e)
        return False


def send_contact_request(telegram_id: int) -> bool:
    """
    Foydalanuvchiga "Telefon raqamni ulash" tugmali klaviatura yuboradi.
    Telegram foydalanuvchi ulashgan raqam unga tegishliligini kafolatlaydi.
    """
    token = getattr(settings, 'TELEGRAM_BOT_TOKEN', '')
    if not token:
        return False

    brand = settings.PROJECT_BRAND_NAME
    try:
        resp = requests.post(
            TELEGRAM_API.format(token=token),
            json={
                'chat_id': telegram_id,
                'text': (f'<b>{brand}</b> saytiga kirish uchun\ntelefon raqamingizni ulashing 👇'),
                'parse_mode': 'HTML',
                'reply_markup': {
                    'keyboard': [
                        [
                            {
                                'text': '📱 Telefon raqamni ulash',
                                'request_contact': True,
                            }
                        ]
                    ],
                    'resize_keyboard': True,
                    'one_time_keyboard': True,
                },
            },
            timeout=8,
        )
        return resp.status_code == 200 and resp.json().get('ok', False)
    except requests.RequestException as e:
        log.error('send_contact_request xatosi: %s', e)
        return False


def send_success_message(telegram_id: int) -> bool:
    """Tasdiqlash muvaffaqiyatli — klaviaturani olib tashlab xabar yuboradi."""
    token = getattr(settings, 'TELEGRAM_BOT_TOKEN', '')
    if not token:
        return False
    brand = settings.PROJECT_BRAND_NAME
    try:
        resp = requests.post(
            TELEGRAM_API.format(token=token),
            json={
                'chat_id': telegram_id,
                'text': f'✅ <b>{brand}</b> ga muvaffaqiyatli kirdingiz!',
                'parse_mode': 'HTML',
                'reply_markup': {'remove_keyboard': True},
            },
            timeout=8,
        )
        return resp.status_code == 200
    except requests.RequestException:
        return False


def send_link_confirmation(telegram_id: int, account_label: str, token: str) -> bool:
    """Ask the Telegram owner to confirm linking this chat to ``account_label``.

    The account is named in the message so someone who was handed another person's
    link can see it is not their account and press "No".
    """
    bot_token = getattr(settings, 'TELEGRAM_BOT_TOKEN', '')
    if not bot_token:
        return False
    try:
        resp = requests.post(
            TELEGRAM_API.format(token=bot_token),
            json={
                'chat_id': telegram_id,
                'text': (
                    f'<b>{settings.PROJECT_BRAND_NAME}</b>\n'
                    f'Telegramingizni <b>{account_label}</b> hisobiga ulaymizmi?\n'
                    'Bu sizning hisobingiz bo\'lmasa, "Yo\'q" ni bosing.'
                ),
                'parse_mode': 'HTML',
                'reply_markup': {
                    'inline_keyboard': [
                        [
                            {'text': '✅ Ha, ulash', 'callback_data': f'tglink:{token}'},
                            {'text': "❌ Yo'q", 'callback_data': f'tgcancel:{token}'},
                        ]
                    ]
                },
            },
            timeout=8,
        )
        return resp.status_code == 200 and resp.json().get('ok', False)
    except requests.RequestException as e:
        log.error('send_link_confirmation xatosi: %s', e)
        return False


def answer_callback(callback_query_id: str, text: str) -> bool:
    bot_token = getattr(settings, 'TELEGRAM_BOT_TOKEN', '')
    if not bot_token:
        return False
    try:
        resp = requests.post(
            f'https://api.telegram.org/bot{bot_token}/answerCallbackQuery',
            json={'callback_query_id': callback_query_id, 'text': text},
            timeout=8,
        )
        return resp.status_code == 200
    except requests.RequestException:
        return False
