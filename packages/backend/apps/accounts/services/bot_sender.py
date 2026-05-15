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
                'chat_id'    : telegram_id,
                'text'       : text,
                'parse_mode' : 'HTML',
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

    brand = getattr(settings, 'PROJECT_BRAND_NAME', 'Milliy Sertifikat')
    try:
        resp = requests.post(
            TELEGRAM_API.format(token=token),
            json={
                'chat_id': telegram_id,
                'text'   : (
                    f'<b>{brand}</b> saytiga kirish uchun\n'
                    f'telefon raqamingizni ulashing 👇'
                ),
                'parse_mode'  : 'HTML',
                'reply_markup': {
                    'keyboard': [[{
                        'text'           : '📱 Telefon raqamni ulash',
                        'request_contact': True,
                    }]],
                    'resize_keyboard' : True,
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
    brand = getattr(settings, 'PROJECT_BRAND_NAME', 'Milliy Sertifikat')
    try:
        resp = requests.post(
            TELEGRAM_API.format(token=token),
            json={
                'chat_id'     : telegram_id,
                'text'        : f'✅ <b>{brand}</b> ga muvaffaqiyatli kirdingiz!',
                'parse_mode'  : 'HTML',
                'reply_markup': {'remove_keyboard': True},
            },
            timeout=8,
        )
        return resp.status_code == 200
    except requests.RequestException:
        return False
