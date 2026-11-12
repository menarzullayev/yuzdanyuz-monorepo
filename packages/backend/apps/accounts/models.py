import datetime
import hashlib
import hmac
import secrets
from uuid import uuid4

from django.conf import settings
from django.contrib.auth.models import AbstractUser, UserManager
from django.db import models
from django.db.models import Case, IntegerField, Value, When
from django.db.models.functions import Lower
from django.utils import timezone
from simple_history.models import HistoricalRecords

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


class CustomUserManager(UserManager):
    """Stores a missing email as NULL: '' would collide on the unique email column."""

    @classmethod
    def normalize_email(cls, email):
        return super().normalize_email(email) or None


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

    # ── Auth state ────────────────────────────────────────────────────────────
    # Telegram/Google signups get a generated username and one free change.
    username_is_auto = models.BooleanField(default=False)
    username_changed_at = models.DateTimeField(null=True, blank=True)
    # Set when an admin issued a temporary password; login is refused until it is replaced.
    must_change_password = models.BooleanField(default=False)
    # Text consent (no checkbox): terms version shown at signup + when.
    terms_version = models.CharField(max_length=20, blank=True)
    terms_accepted_at = models.DateTimeField(null=True, blank=True)

    objects = CustomUserManager()

    # ── Django AbstractUser REQUIRED_FIELDS ni tozalaymiz ────────────────────
    # Multi-auth da email majburiy emas.
    REQUIRED_FIELDS = []

    # ISSUE-401: SOC2 audit trail (historical_customuser shadow table)
    # excluded_fields: password hash, last_login — har login'da yangi row
    # yaratilmasin (kerakmas, security loglarda allaqachon).
    history = HistoricalRecords(excluded_fields=['password', 'last_login'])

    class Meta:
        verbose_name = 'Foydalanuvchi'
        verbose_name_plural = 'Foydalanuvchilar'
        ordering = ['-date_joined']
        constraints = [
            models.UniqueConstraint(Lower('username'), name='uniq_user_username_ci'),
        ]

    def __str__(self):
        return self.display_name

    def save(self, *args, **kwargs):
        # set_password() leaves the raw password in _password until the next save.
        password_changed = self._password is not None and not self._state.adding
        super().save(*args, **kwargs)
        if password_changed:
            self._revoke_sessions_after_password_change()

    def _revoke_sessions_after_password_change(self) -> None:
        """Every path that changes a password (API, admin, manage.py changepassword)
        signs out all sessions; the API change view may spare the caller's own session
        by setting ``_keep_session_id`` first."""
        from apps.accounts.services.token_service import revoke_user_sessions

        keep = getattr(self, '_keep_session_id', None)
        self._keep_session_id = None
        revoke_user_sessions(self, except_session_id=keep, reason='password')

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
        membership = self.primary_membership
        if membership is None or membership.organization.org_type == 'platform':
            # The platform org only carries B2C users; it is not a real organization.
            return 'b2c'
        return membership.role.name

    @property
    def primary_membership(self):
        """Asosiy tashkilot bilan bog'liq membership.

        A real organization always wins over the built-in platform org (which every B2C
        user belongs to); inside the same kind: is_primary first, then the newest join.
        """
        return (
            self.memberships.filter(status='active')
            .select_related('role', 'organization')
            .annotate(
                platform_rank=Case(
                    When(organization__org_type='platform', then=Value(1)),
                    default=Value(0),
                    output_field=IntegerField(),
                )
            )
            .order_by('platform_rank', '-is_primary', '-joined_at')
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
    # HMAC of the code (never the code itself); see hash_code().
    code_hash = models.CharField(max_length=64)
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

    @staticmethod
    def hash_code(phone: str, code: str) -> str:
        key = settings.SECRET_KEY.encode()
        return hmac.new(key, f'otp:{phone}:{code}'.encode(), hashlib.sha256).hexdigest()

    def matches(self, code: str) -> bool:
        return hmac.compare_digest(self.code_hash, self.hash_code(self.phone, code))

    @classmethod
    def create_for_phone(cls, phone: str) -> tuple['OTPCode', str]:
        """Create a new OTP; returns ``(row, plaintext_code)``. Only the hash is stored."""
        code = f'{secrets.randbelow(10**6):06d}'
        otp = cls.objects.create(
            phone=phone,
            code_hash=cls.hash_code(phone, code),
            expires_at=timezone.now() + datetime.timedelta(seconds=OTP_TTL_SECONDS),
        )
        return otp, code


# ─── Device sessions ──────────────────────────────────────────────────────────


class DeviceSession(models.Model):
    """One signed-in browser/app of a user (the unit behind a refresh token).

    A device is identified by a random id kept in an HttpOnly cookie, never by
    IP or User-Agent. The refresh token is ``<session id>.<secret>``; only the
    secret's SHA-256 is stored, and the previous hash is kept for a short
    grace window so two tabs refreshing at once do not log each other out.
    """

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='device_sessions'
    )
    device_id = models.CharField(max_length=64)
    refresh_hash = models.CharField(max_length=64)
    prev_refresh_hash = models.CharField(max_length=64, blank=True)
    rotated_at = models.DateTimeField(null=True, blank=True)
    remember = models.BooleanField(default=False)

    user_agent = models.CharField(max_length=300, blank=True)
    device_label = models.CharField(max_length=120, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    country = models.CharField(max_length=2, blank=True)
    city = models.CharField(max_length=100, blank=True)

    created_at = models.DateTimeField(default=timezone.now)
    last_used_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_reason = models.CharField(max_length=20, blank=True)

    class Meta:
        verbose_name = 'Qurilma sessiyasi'
        verbose_name_plural = 'Qurilma sessiyalari'
        ordering = ['-last_used_at']
        indexes = [models.Index(fields=['user', 'revoked_at'], name='accounts_ds_user_rev_idx')]
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'device_id'],
                condition=models.Q(revoked_at__isnull=True),
                name='uniq_active_session_per_device',
            )
        ]

    def __str__(self):
        return f'{self.user_id} / {self.device_label or self.device_id[:8]}'

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None and self.expires_at > timezone.now()


# ─── Two-factor authentication ────────────────────────────────────────────────


class TOTPDevice(models.Model):
    """The user's authenticator-app secret (encrypted); active once ``confirmed_at`` is set."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='totp_device'
    )
    secret_enc = models.TextField()
    confirmed_at = models.DateTimeField(null=True, blank=True)
    # Highest accepted 30 s step: a code can be used only once.
    last_used_step = models.BigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'TOTP qurilma'
        verbose_name_plural = 'TOTP qurilmalar'

    def __str__(self):
        return f'TOTP {self.user_id} ({"on" if self.confirmed_at else "pending"})'


class BackupCode(models.Model):
    """One-time 2FA backup code, stored as a keyed hash."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='backup_codes'
    )
    code_hash = models.CharField(max_length=64)
    used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Zaxira kod'
        verbose_name_plural = 'Zaxira kodlar'
        indexes = [models.Index(fields=['user', 'used_at'], name='accounts_bc_user_used_idx')]

    def __str__(self):
        return f'backup code {self.pk} ({"used" if self.used_at else "free"})'


# ─── Password recovery ────────────────────────────────────────────────────────


class RecoveryToken(models.Model):
    """One-time password-recovery link token (sent through the Telegram bot).

    Only the SHA-256 of the token is stored; it is valid for 15 minutes and
    can be used once.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='recovery_tokens'
    )
    token_hash = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'Tiklash tokeni'
        verbose_name_plural = 'Tiklash tokenlari'
        ordering = ['-created_at']

    def __str__(self):
        return f'recovery {self.user_id} ({"used" if self.used_at else "open"})'


class PasswordResetRequest(models.Model):
    """Request queue for platform admins when no other recovery channel works."""

    class Status(models.TextChoices):
        PENDING = 'pending', 'Kutilmoqda'
        DONE = 'done', 'Bajarildi'
        REJECTED = 'rejected', 'Rad etildi'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='password_reset_requests',
    )
    username_entered = models.CharField(max_length=150)
    contact_note = models.CharField(max_length=300, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    handled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='handled_reset_requests',
    )
    handled_at = models.DateTimeField(null=True, blank=True)
    admin_note = models.CharField(max_length=300, blank=True)

    class Meta:
        verbose_name = 'Parol tiklash so\u02bbrovi'
        verbose_name_plural = 'Parol tiklash so\u02bbrovlari'
        ordering = ['-created_at']
        indexes = [models.Index(fields=['status', 'created_at'], name='accounts_prr_status_idx')]

    def __str__(self):
        return f'{self.username_entered} ({self.status})'


# ─── Onboarding: reference data (tables with admin, filled in later) ──────────


class ExamGoal(models.Model):
    """An exam a user can prepare for (DTM, national certificate, IELTS, ...)."""

    code = models.SlugField(max_length=40, unique=True)
    name_uz = models.CharField(max_length=100)
    name_ru = models.CharField(max_length=100, blank=True)
    name_en = models.CharField(max_length=100, blank=True)
    # Goals that need a DTM direction or a subject pair at onboarding.
    requires_direction = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        verbose_name = 'Imtihon maqsadi'
        verbose_name_plural = 'Imtihon maqsadlari'
        ordering = ['sort_order', 'code']

    def __str__(self):
        return self.name_uz


class DTMDirection(models.Model):
    """A DTM study direction with its two profile subjects."""

    code = models.CharField(max_length=40, unique=True)
    name_uz = models.CharField(max_length=200)
    name_ru = models.CharField(max_length=200, blank=True)
    name_en = models.CharField(max_length=200, blank=True)
    subject_1 = models.CharField(max_length=60)
    subject_2 = models.CharField(max_length=60)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'DTM yo\u02bbnalishi'
        verbose_name_plural = 'DTM yo\u02bbnalishlari'
        ordering = ['name_uz']

    def __str__(self):
        return self.name_uz


class DirectionPassingScore(models.Model):
    direction = models.ForeignKey(DTMDirection, on_delete=models.CASCADE, related_name='scores')
    year = models.PositiveSmallIntegerField()
    grant_score = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    contract_score = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    source_url = models.URLField(blank=True)

    class Meta:
        verbose_name = 'O\u02bbtish bali'
        verbose_name_plural = 'O\u02bbtish ballari'
        ordering = ['direction', '-year']
        constraints = [
            models.UniqueConstraint(fields=['direction', 'year'], name='uniq_direction_year')
        ]

    def __str__(self):
        return f'{self.direction_id} / {self.year}'


class ExamCalendarEvent(models.Model):
    exam_goal = models.ForeignKey(ExamGoal, on_delete=models.CASCADE, related_name='events')
    title = models.CharField(max_length=200)
    date = models.DateField()
    registration_deadline = models.DateField(null=True, blank=True)
    region = models.ForeignKey(Region, null=True, blank=True, on_delete=models.SET_NULL)
    notes = models.CharField(max_length=300, blank=True)

    class Meta:
        verbose_name = 'Imtihon kalendari'
        verbose_name_plural = 'Imtihon kalendari'
        ordering = ['date']

    def __str__(self):
        return f'{self.title} ({self.date})'


# ─── Onboarding: user answers ─────────────────────────────────────────────────


class TestLanguage(models.TextChoices):
    UZ = 'uz', "O'zbek"
    RU = 'ru', 'Русский'
    KAA = 'kaa', 'Qoraqalpoq'


class OnboardingProfile(models.Model):
    """What we ask a user before showing their first RESULT (not at signup)."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='onboarding'
    )
    goals = models.ManyToManyField(ExamGoal, blank=True, related_name='profiles')
    primary_goal = models.ForeignKey(
        ExamGoal, null=True, blank=True, on_delete=models.SET_NULL, related_name='+'
    )
    dtm_direction = models.ForeignKey(
        DTMDirection, null=True, blank=True, on_delete=models.SET_NULL, related_name='+'
    )
    subject_1 = models.CharField(max_length=60, blank=True)
    subject_2 = models.CharField(max_length=60, blank=True)
    test_language = models.CharField(max_length=3, choices=TestLanguage.choices, blank=True)
    target_grade = models.CharField(max_length=3, blank=True)
    exam_date = models.DateField(null=True, blank=True)
    graduation_year = models.PositiveSmallIntegerField(null=True, blank=True)
    birth_date = models.DateField(null=True, blank=True)
    parent_telegram = models.CharField(max_length=40, blank=True)
    # First-touch attribution captured automatically (UTM / bot start param) ...
    acquisition = models.JSONField(default=dict, blank=True)
    # ... and the optional "how did you hear about us" answer.
    acquisition_answer = models.CharField(max_length=20, blank=True)
    acquisition_note = models.CharField(max_length=200, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Onboarding profili'
        verbose_name_plural = 'Onboarding profillari'

    def __str__(self):
        return f'onboarding {self.user_id}'


class ExamCertificate(models.Model):
    """A certificate the user already holds (type, level, date)."""

    class CertType(models.TextChoices):
        MILLIY = 'milliy_sertifikat', 'Milliy sertifikat'
        IELTS = 'ielts', 'IELTS'
        CEFR = 'cefr', 'CEFR'
        TOEFL = 'toefl', 'TOEFL'
        SAT = 'sat', 'SAT'
        OTHER = 'other', 'Boshqa'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='external_certificates'
    )
    cert_type = models.CharField(max_length=20, choices=CertType.choices)
    subject = models.CharField(max_length=60, blank=True)
    level = models.CharField(max_length=20)
    date = models.DateField()

    class Meta:
        verbose_name = 'Sertifikat'
        verbose_name_plural = 'Sertifikatlar'
        ordering = ['-date']

    def __str__(self):
        return f'{self.cert_type} {self.level}'


class OrgResultsConsent(models.Model):
    """The user's explicit consent that an organization sees their results.

    Deliberately NOT a tenant model (like Membership): it is written while the
    user has no tenant context yet (joining by invite code). Organizations read
    it only through their own Membership rows.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='org_consents'
    )
    organization = models.ForeignKey(
        'organizations.Organization', on_delete=models.CASCADE, related_name='results_consents'
    )
    consented_at = models.DateTimeField(default=timezone.now)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'Natijalarni ko\u02bbrish roziligi'
        verbose_name_plural = 'Natijalarni ko\u02bbrish rozilig\u2018i'
        constraints = [
            models.UniqueConstraint(fields=['user', 'organization'], name='uniq_org_consent')
        ]

    def __str__(self):
        return f'{self.user_id} -> {self.organization_id}'
