"""Starter typing texts (one per language). Staff edit/extend them in the admin;
the Cyrillic and Karakalpak texts should be reviewed by a native speaker."""

from django.db import migrations

TEXTS = [
    (
        'uz-Latn',
        "O'zbekiston haqida",
        "O'zbekiston Markaziy Osiyoda joylashgan davlat. Uning poytaxti Toshkent shahri. "
        "Mamlakatimiz o'zining qadimiy shaharlari, mehmondo'st xalqi va boy madaniyati bilan mashhur.",
    ),
    (
        'uz-Cyrl',
        'Ўзбекистон ҳақида',
        'Ўзбекистон Марказий Осиёда жойлашган давлат. Унинг пойтахти Тошкент шаҳри. '
        'Мамлакатимиз ўзининг қадимий шаҳарлари, меҳмондўст халқи ва бой маданияти билан машҳур.',
    ),
    (
        'ru',
        'Об Узбекистане',
        'Узбекистан расположен в Центральной Азии. Его столица город Ташкент. '
        'Страна известна древними городами, гостеприимным народом и богатой культурой.',
    ),
    (
        'en',
        'About Uzbekistan',
        'Uzbekistan is a country in Central Asia. Its capital is the city of Tashkent. '
        'The country is known for its ancient cities, hospitable people and rich culture.',
    ),
    (
        'kaa',
        'Ózbekstan haqqında',
        'Ózbekstan Orayliq Aziyada jaylasqan mámleket. Onıń paytaxtı Tashkent qalası. '
        'Mámleketimiz ejelgi qalaları, miymandós xalqı hám bay mádeniyatı menen belgili.',
    ),
]


def seed(apps, schema_editor):
    TypingText = apps.get_model('engagement', 'TypingText')
    for language, title, body in TEXTS:
        TypingText.objects.get_or_create(language=language, title=title, defaults={'body': body})


def unseed(apps, schema_editor):
    TypingText = apps.get_model('engagement', 'TypingText')
    TypingText.objects.filter(title__in=[t[1] for t in TEXTS], runs__isnull=True).delete()


class Migration(migrations.Migration):
    dependencies = [('engagement', '0005_arena_rating_rls')]
    operations = [migrations.RunPython(seed, unseed)]
