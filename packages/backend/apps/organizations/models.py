from uuid import uuid4

from django.conf import settings as django_settings
from django.db import models
from django.utils import timezone

# ─── Org settings default qiymatlari ─────────────────────────────────────────
# effective_settings() orqali har doim to'liq holda o'qiladi.
# Har bir org o'z settings JSONField da faqat o'zgartirilgan qiymatlarni saqlaydi.
DEFAULT_ORG_SETTINGS = {
    'approval_required': False,  # True bo'lsa yangi orglar pending da qoladi
    'ai_enabled': True,  # AI diagnostika va feedback yoqilganmi
    'max_students': None,  # None = cheksiz
    'proctoring': 'basic',  # 'none' | 'basic' | 'strict'
    'allow_public_exams': True,  # B2C ochiq testlarini ko'rsatishga ruxsat
    'trial_days': 14,  # Yangi org uchun sinov davri (kun)
}

# ─── Org ichidagi tizimiy rollar va ularning default ruxsatlari ───────────────
# Bu rollar har yangi Organization yaratilganda avtomatik signal orqali qo'shiladi.
SYSTEM_ROLES = {
    'owner': {
        'display_name': 'Egasi',
        'permissions': ['*'],  # barcha ruxsatlar
    },
    'manager': {
        'display_name': 'Menejer',
        'permissions': [
            'exam:create',
            'exam:edit',
            'exam:delete',
            'exam:view',
            'catalog:create',
            'catalog:edit',
            'catalog:view',
            'members:invite',
            'members:manage',
            'members:view',
            'analytics:view',
        ],
    },
    'teacher': {
        'display_name': "O'qituvchi",
        'permissions': [
            'exam:create',
            'exam:edit',
            'exam:view',
            'catalog:create',
            'catalog:edit',
            'catalog:view',
            'members:view',
            'analytics:view',
        ],
    },
    'student': {
        'display_name': "O'quvchi",
        'permissions': [
            'exam:take',
            'exam:view_own_results',
            'catalog:view',
        ],
    },
    'observer': {
        'display_name': 'Kuzatuvchi',
        'permissions': [
            'exam:view',
            'analytics:view',
            'members:view',
        ],
    },
}


# ─── Enums ────────────────────────────────────────────────────────────────────


class OrgType(models.TextChoices):
    PLATFORM = 'platform', 'Platforma'
    TENANT = 'tenant', "O'quv markazi"
    BRANCH = 'branch', 'Filial'


class OrgStatus(models.TextChoices):
    PENDING = 'pending', 'Tasdiqlash kutilmoqda'
    TRIAL = 'trial', 'Sinov davri'
    ACTIVE = 'active', 'Faol'
    SUSPENDED = 'suspended', "To'xtatilgan"
    CANCELLED = 'cancelled', 'Bekor qilingan'


class OrgTier(models.TextChoices):
    FREE = 'free', 'Bepul'
    BASIC = 'basic', 'Asosiy'
    PRO = 'pro', 'Professional'
    ENTERPRISE = 'enterprise', 'Korporativ'


class MembershipStatus(models.TextChoices):
    ACTIVE = 'active', 'Faol'
    INVITED = 'invited', 'Taklif yuborilgan'
    SUSPENDED = 'suspended', "To'xtatilgan"
    LEFT = 'left', 'Chiqib ketgan'


class JoinedVia(models.TextChoices):
    MANUAL = 'manual', "Admin qo'shdi"
    INVITE = 'invite', 'Invite link'
    API = 'api', 'API integratsiya'


# ─── Organization ─────────────────────────────────────────────────────────────


class Organization(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    name = models.CharField(max_length=255, verbose_name='Nomi')
    slug = models.SlugField(max_length=100, unique=True, verbose_name='Slug')

    # Daraja: platforma → tenant → branch
    org_type = models.CharField(
        max_length=20,
        choices=OrgType.choices,
        default=OrgType.TENANT,
        verbose_name='Tur',
    )
    parent = models.ForeignKey(
        'self',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='children',
        verbose_name='Yuqori tashkilot',
    )

    # Lifecycle
    status = models.CharField(
        max_length=20,
        choices=OrgStatus.choices,
        default=OrgStatus.PENDING,
        verbose_name='Holat',
    )
    tier = models.CharField(
        max_length=20,
        choices=OrgTier.choices,
        default=OrgTier.FREE,
        verbose_name='Tarif',
    )
    trial_ends_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Sinov tugash vaqti',
    )

    # White-label branding
    logo = models.ImageField(upload_to='orgs/logos/', null=True, blank=True)
    primary_color = models.CharField(max_length=7, default='#1A73E8', verbose_name='Asosiy rang')
    subdomain = models.SlugField(max_length=100, null=True, blank=True, unique=True)
    custom_domain = models.CharField(max_length=255, null=True, blank=True, unique=True)

    # Device policy — bir qurilma yoki ko'p qurilmaga ruxsat berish
    single_device_policy = models.BooleanField(
        default=True,
        verbose_name='Bir qurilma siyosati',
        help_text='True: 1 ta qurilma faol, yangi kirsa eski sessiya uzilib qoladi',
    )

    # Konfiguratsiya (DEFAULT_ORG_SETTINGS bilan merge qilinadi)
    settings = models.JSONField(default=dict, blank=True, verbose_name='Sozlamalar')

    # Kim yaratdi
    created_by = models.ForeignKey(
        django_settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='created_orgs',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Tashkilot'
        verbose_name_plural = 'Tashkilotlar'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.name} ({self.get_org_type_display()})'

    # ── Computed properties ───────────────────────────────────────────────────

    @property
    def is_active(self) -> bool:
        return self.status == OrgStatus.ACTIVE

    @property
    def is_trial(self) -> bool:
        return self.status == OrgStatus.TRIAL

    @property
    def trial_expired(self) -> bool:
        if self.trial_ends_at is None:
            return False
        return timezone.now() > self.trial_ends_at

    @property
    def effective_settings(self) -> dict:
        """DEFAULT_ORG_SETTINGS ustiga org o'z settings ini yozib beradi."""
        return {**DEFAULT_ORG_SETTINGS, **self.settings}

    def get_setting(self, key: str):
        return self.effective_settings.get(key)


# ─── OrgRole (RBAC) ───────────────────────────────────────────────────────────


class OrgRole(models.Model):
    """
    Org ichidagi custom rol.
    is_system_role=True rollar o'chirib bo'lmaydi va avtomatik yaratiladi.
    permissions = ['exam:create', 'billing:view', '*'] (wildcard = hammasi)
    """

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name='roles',
        verbose_name='Tashkilot',
    )
    name = models.CharField(max_length=50, verbose_name='Kod nomi')
    display_name = models.CharField(max_length=100, verbose_name="Ko'rinadigan nomi")
    permissions = models.JSONField(default=list, verbose_name='Ruxsatlar')
    is_system_role = models.BooleanField(
        default=False,
        help_text="Tizimiy rollar o'chirib bo'lmaydi",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Rol'
        verbose_name_plural = 'Rollar'
        unique_together = [['organization', 'name']]
        ordering = ['name']

    def __str__(self):
        return f'{self.organization.slug}:{self.name}'

    def has_permission(self, perm: str) -> bool:
        """
        Permission tekshirish. Tartib:
          1. '*' — barcha ruxsatlar
          2. Exact match: 'exam:create'
          3. Resource wildcard: 'exam:*' → 'exam:create' ✓

        Format: 'resource:action' (colon notation)
        """
        if '*' in self.permissions:
            return True
        if perm in self.permissions:
            return True
        # 'exam:*' → 'exam:create' wildcard
        if ':' in perm:
            resource = perm.split(':')[0]
            if f'{resource}:*' in self.permissions:
                return True
        return False


# ─── Membership ───────────────────────────────────────────────────────────────


class Membership(models.Model):
    """
    User ↔ Organization ko'prigi.
    Bir user bir nechta orgga tegishli bo'la oladi.
    is_primary — leaderboard, billing, default org uchun.
    """

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    user = models.ForeignKey(
        django_settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='memberships',
    )
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name='memberships',
    )
    role = models.ForeignKey(
        OrgRole,
        on_delete=models.PROTECT,
        related_name='memberships',
        verbose_name='Rol',
    )
    status = models.CharField(
        max_length=20,
        choices=MembershipStatus.choices,
        default=MembershipStatus.INVITED,
    )
    joined_via = models.CharField(
        max_length=20,
        choices=JoinedVia.choices,
        default=JoinedVia.MANUAL,
    )
    is_primary = models.BooleanField(
        default=False,
        help_text='Foydalanuvchining asosiy tashkiloti',
    )
    invited_by = models.ForeignKey(
        django_settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='sent_invites',
    )
    joined_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "A'zolik"
        verbose_name_plural = "A'zoliklar"
        unique_together = [['user', 'organization']]
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.user} → {self.organization.slug} ({self.role.name})'

    def has_permission(self, perm: str) -> bool:
        return self.role.has_permission(perm)

    def activate(self):
        self.status = MembershipStatus.ACTIVE
        self.joined_at = timezone.now()
        self.save(update_fields=['status', 'joined_at', 'updated_at'])


# ─── OrgInvite ────────────────────────────────────────────────────────────────


class OrgInvite(models.Model):
    """
    Invite link/token tizimi.
    max_uses=None → cheksiz foydalanish.
    expires_at=None → muddatsiz.
    """

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name='invites',
    )
    token = models.UUIDField(default=uuid4, unique=True, editable=False)
    role = models.ForeignKey(
        OrgRole,
        on_delete=models.CASCADE,
        related_name='invites',
    )
    max_uses = models.PositiveIntegerField(null=True, blank=True)
    used_count = models.PositiveIntegerField(default=0)
    expires_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        django_settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='created_invites',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Taklif'
        verbose_name_plural = 'Takliflar'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.organization.slug} invite ({self.token})'

    @property
    def is_valid(self) -> bool:
        if not self.is_active:
            return False
        if self.max_uses is not None and self.used_count >= self.max_uses:
            return False
        if self.expires_at is not None and timezone.now() > self.expires_at:
            return False
        return True

    def use(self):
        """Invite ishlatilganda counter oshiriladi."""
        self.used_count += 1
        self.save(update_fields=['used_count'])


# ─── Signal: yangi Org yaratilganda tizimiy rollarni avtomatik qo'shish ───────

from django.db.models.signals import post_save
from django.dispatch import receiver


@receiver(post_save, sender=Organization)
def create_system_roles(sender, instance, created, **kwargs):
    if not created:
        return
    for role_name, data in SYSTEM_ROLES.items():
        OrgRole.objects.create(
            organization=instance,
            name=role_name,
            display_name=data['display_name'],
            permissions=data['permissions'],
            is_system_role=True,
        )
