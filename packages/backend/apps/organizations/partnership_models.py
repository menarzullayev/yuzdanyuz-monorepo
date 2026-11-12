"""Platform-level CRM model for partnership applications (schools / learning centres).

NOT a tenant model: applicants are not organizations yet, so there is no
organization FK and no RLS. Only platform staff can read it (see partnership_views).
"""

from uuid import uuid4

from django.conf import settings as django_settings
from django.db import models
from simple_history.models import HistoricalRecords


class ApplicantType(models.TextChoices):
    SCHOOL = 'school', 'Maktab'
    LEARNING_CENTRE = 'learning_centre', "O'quv markazi"


class ApplicationStatus(models.TextChoices):
    NEW = 'new', 'Yangi'
    CONTACTED = 'contacted', "Bog'lanildi"
    AGREED = 'agreed', 'Kelishildi'
    REJECTED = 'rejected', 'Rad etildi'


# Allowed manual status moves. REJECTED can be reopened as NEW; every other
# backwards move is refused so the funnel statistics stay meaningful.
ALLOWED_STATUS_TRANSITIONS = {
    ApplicationStatus.NEW: {ApplicationStatus.CONTACTED, ApplicationStatus.REJECTED},
    ApplicationStatus.CONTACTED: {ApplicationStatus.AGREED, ApplicationStatus.REJECTED},
    ApplicationStatus.AGREED: {ApplicationStatus.REJECTED},
    ApplicationStatus.REJECTED: {ApplicationStatus.NEW},
}


class PartnershipApplication(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)

    # What the applicant submitted (public form)
    applicant_type = models.CharField(max_length=20, choices=ApplicantType.choices)
    name = models.CharField(max_length=200, verbose_name='Tashkilot nomi')
    contact_person = models.CharField(max_length=120)
    phone = models.CharField(max_length=20, db_index=True, help_text='Normalized +998XXXXXXXXX')
    email = models.EmailField(blank=True)
    region = models.CharField(max_length=100)
    students_count = models.PositiveIntegerField()
    message = models.TextField(blank=True, max_length=2000)

    # CRM state (staff only)
    status = models.CharField(
        max_length=20,
        choices=ApplicationStatus.choices,
        default=ApplicationStatus.NEW,
        db_index=True,
    )
    notes = models.TextField(blank=True)
    next_call_date = models.DateField(null=True, blank=True, db_index=True)
    assigned_to = models.ForeignKey(
        django_settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='assigned_partnership_applications',
    )
    contacted_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Who moved the funnel and when (SOC2-style audit trail, same as Membership)
    history = HistoricalRecords()

    class Meta:
        verbose_name = 'Hamkorlik arizasi'
        verbose_name_plural = 'Hamkorlik arizalari'
        ordering = ['-created_at']
        indexes = [models.Index(fields=['status', 'next_call_date'])]

    def __str__(self):
        return f'{self.name} ({self.get_status_display()})'
