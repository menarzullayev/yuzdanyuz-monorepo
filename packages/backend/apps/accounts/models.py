import datetime
from uuid import uuid4

from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone

# ─── Region / District ────────────────────────────────────────────────────────
# Leaderboard (viloyat reytingi), analitika va org joylashuvi uchun umumiy jadval.


class Region(models.Model):
    code = models.PositiveSmallIntegerField(unique=True, verbose_name='Kod')
    name_uz = models.CharField(max_length=100, verbose_name="Nomi (o'zb)")
    name_ru = models.CharField(max_length=100, verbose_name='Nomi (rus)')
    name_en = models.CharField(max_length=100, verbose_name='Nomi (eng)')
    slug = models.SlugField(max_length=60, unique=True)

    class Meta:
        verbose_name = 'Viloyat'
        verbose_name_plural = 'Viloyatlar'
        ordering = ['code']

    def __str__(self):
        return self.name_uz


class District(models.Model):
    region = models.ForeignKey(Region, on_delete=models.CASCADE, related_name='districts')
    code = models.PositiveSmallIntegerField(verbose_name='Kod')
    name_uz = models.CharField(max_length=100, verbose_name="Nomi (o'zb)")
    name_ru = models.CharField(max_length=100, verbose_name='Nomi (rus)')
    name_en = models.CharField(max_length=100, verbose_name='Nomi (eng)')

    class Meta:
        verbose_name = 'Tuman'
        verbose_name_plural = 'Tumanlar'
        unique_together = [['region', 'code']]
        ordering = ['region', 'code']

    def __str__(self):
        return f'{self.region.name_uz} / {self.name_uz}'


# ─── Enums ────────────────────────────────────────────────────────────────────


class PreferredLang(models.TextChoices):
    UZ = 'uz', "O'zbek"
    RU = 'ru', 'Русский'
    EN = 'en', 'English'


class StudyYear(models.TextChoices):
    GRADE_10 = 'grade_10', '10-sinf'
    GRADE_11 = 'grade_11', '11-sinf'
    GRADUATE = 'graduate', 'Bitiruvchi'
    UNIVERSITY = 'university', 'Talaba'
    TEACHER = 'teacher', "O'qituvchi"
    OTHER = 'other', 'Boshqa'


# ─── CustomUser ───────────────────────────────────────────────────────────────


class CustomUser(AbstractUser):
    """
    Platforma foydalanuvchisi.

    Login usullari (kamida bittasi bo'lishi shart):
        phone_number | email | telegram_id

    user_type — Membership dan derive qilinadi (@property).
    Org ichidagi rol — Membership.role (OrgRole) orqali boshqariladi.
    """

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)

    # ── Asosiy login identifikatorlari ───────────────────────────────────────
    # Django AbstractUser da email blank=True (unique emas). Biz unique qilamiz.
    email = models.EmailField(blank=True, null=True, unique=True, verbose_name='Email')
    phone_number = models.CharField(
        max_length=20,
        blank=True,
        null=True,
        unique=True,
        verbose_name='Telefon raqami',
        help_text='+998901234567 formatida',
    )

    # ── Telegram TMA integratsiyasi ───────────────────────────────────────────
    telegram_id = models.BigIntegerField(null=True, blank=True, unique=True)
    telegram_username = models.CharField(max_length=100, null=True, blank=True)
    telegram_photo_url = models.URLField(max_length=500, null=True, blank=True)
    telegram_language = models.CharField(max_length=10, null=True, blank=True)
    tg_linked_at = models.DateTimeField(null=True, blank=True)

    # ── Profil ────────────────────────────────────────────────────────────────
    # first_name va last_name AbstractUser da bor (max_length=150).
    # Biz ularni kengaytiramiz: verbose_name + max_length kamaytirish kerak emas.
    avatar = models.ImageField(upload_to='users/avatars/', null=True, blank=True)

    preferred_lang = models.CharField(
        max_length=5,
        choices=PreferredLang.choices,
        default=PreferredLang.UZ,
        verbose_name='Til',
    )

    # ── Hududiy ma'lumotlar ───────────────────────────────────────────────────
    region = models.ForeignKey(
        Region,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='users',
        verbose_name='Viloyat',
    )
    district = models.ForeignKey(
        District,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='users',
        verbose_name='Tuman',
    )

    # ── Akademik ma'lumotlar (AI diagnostika uchun) ───────────────────────────
    birth_year = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        verbose_name="Tug'ilgan yil",
    )
    study_year = models.CharField(
        max_length=20,
        choices=StudyYear.choices,
        null=True,
        blank=True,
        verbose_name="O'qish bosqichi",
    )
    target_score = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        verbose_name='Maqsad ball (DTM)',
        help_text='Maksimal 189',
    )

    # ── Django AbstractUser REQUIRED_FIELDS ni tozalaymiz ────────────────────
    # Multi-auth da email majburiy emas.
    REQUIRED_FIELDS = []

    class Meta:
        verbose_name = 'Foydalanuvchi'
        verbose_name_plural = 'Foydalanuvchilar'
        ordering = ['-date_joined']

    def __str__(self):
        return self.display_name

    # ── Computed properties ───────────────────────────────────────────────────

    @property
    def display_name(self) -> str:
        """Admin paneli, leaderboard va chat uchun ko'rinadigan ism."""
        full = f'{self.first_name} {self.last_name}'.strip()
        return full or self.telegram_username or self.phone_number or self.username

    @property
    def user_type(self) -> str:
        """
        Membership dan derive qilinadi — alohida field yo'q.

        Qaytaradigan qiymatlar:
            'platform_admin'  — is_superuser=True
            'platform_staff'  — is_staff=True
            'b2c'             — hech qanday faol membership yo'q
            '<role_name>'     — asosiy membership ning rol nomi
                                (masalan: 'student', 'teacher', 'owner')
        """
        if self.is_superuser:
            return 'platform_admin'
        if self.is_staff:
            return 'platform_staff'
        membership = (
            self.memberships.filter(status='active')
            .select_related('role')
            .order_by('-is_primary', '-joined_at')
            .first()
        )
        if membership is None:
            return 'b2c'
        return membership.role.name

    @property
    def primary_membership(self):
        """Asosiy tashkilot bilan bog'liq membership."""
        return (
            self.memberships.filter(status='active')
            .select_related('role', 'organization')
            .order_by('-is_primary', '-joined_at')
            .first()
        )

    @property
    def primary_organization(self):
        m = self.primary_membership
        return m.organization if m else None

    @property
    def is_b2c(self) -> bool:
        return self.user_type == 'b2c'

    @property
    def is_profile_complete(self) -> bool:
        """Minimal to'liq profil: ism + (telefon yoki email yoki telegram)."""
        has_name = bool(self.first_name)
        has_contact = bool(self.phone_number or self.email or self.telegram_id)
        return has_name and has_contact

    def has_org_permission(self, org, perm: str) -> bool:
        """
        Berilgan tashkilotda permission borligini tekshiradi.
        Membership → OrgRole → has_permission() zanjiri.

        Format: 'resource:action' (colon notation)
        Example: user.has_org_permission(org, 'exam:create')
        """
        if self.is_superuser:
            return True
        membership = (
            self.memberships.filter(organization=org, status='active')
            .select_related('role')
            .first()
        )
        if membership is None:
            return False
        return membership.has_permission(perm)


# ─── OTP ──────────────────────────────────────────────────────────────────────

OTP_TTL_SECONDS = 120  # 2 daqiqa
OTP_MAX_ATTEMPTS = 3  # 3 marta noto'g'ri → blok


class OTPCode(models.Model):
    """
    Telefon orqali kirish uchun bir martalik kod.

    Har bir send_otp() chaqiruvida yangi yozuv yaratiladi.
    is_verified=True → foydalanilgan, qayta ishlatib bo'lmaydi.
    Eskirgan yozuvlar Celery periodic task orqali tozalanadi (Task 9).
    """

    phone = models.CharField(max_length=20, db_index=True)
    code = models.CharField(max_length=6)
    is_verified = models.BooleanField(default=False)
    attempts = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()

    class Meta:
        verbose_name = 'OTP Kod'
        verbose_name_plural = 'OTP Kodlar'
        ordering = ['-created_at']
        indexes = [models.Index(fields=['phone', 'expires_at'])]

    def __str__(self):
        return f'{self.phone} — {self.created_at:%H:%M:%S}'

    @property
    def is_expired(self) -> bool:
        return timezone.now() > self.expires_at

    @property
    def is_exhausted(self) -> bool:
        return self.attempts >= OTP_MAX_ATTEMPTS

    @classmethod
    def create_for_phone(cls, phone: str) -> 'OTPCode':
        import random

        code = f'{random.randint(0, 999999):06d}'
        return cls.objects.create(
            phone=phone,
            code=code,
            expires_at=timezone.now() + datetime.timedelta(seconds=OTP_TTL_SECONDS),
        )
