"""Per-organization settings for the school / learning-centre rating."""

import uuid

from django.db import models
from django.utils.translation import gettext_lazy as _

from core.mixins import TenantTimestampMixin


class OrgRatingProfile(TenantTimestampMixin):
    """Kept in engagement (not on Organization) so the rating ships without touching
    the organizations app. A missing row means the defaults below."""

    class Kind(models.TextChoices):
        SCHOOL = 'school', _('Maktab')
        LEARNING_CENTRE = 'learning_centre', _("O'quv markazi")

    class Visibility(models.TextChoices):
        PUBLIC = 'public', _('Ommaviy')
        HIDDEN = 'hidden', _('Yashirin')

    DEFAULT_KIND = Kind.LEARNING_CENTRE
    DEFAULT_VISIBILITY = Visibility.PUBLIC

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    kind = models.CharField(max_length=16, choices=Kind.choices, default=DEFAULT_KIND)
    visibility = models.CharField(
        max_length=8, choices=Visibility.choices, default=DEFAULT_VISIBILITY
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['organization'], name='orgratingprofile_one_per_org'),
        ]

    def __str__(self):
        return f'{self.organization_id} {self.kind}/{self.visibility}'
