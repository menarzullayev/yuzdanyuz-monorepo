"""Content plan REST API (staff / org content admins)."""

from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import ContentTarget, Subject
from .services import content_plan as plan

MAX_TARGET = 100_000


class IsContentAdmin(BasePermission):
    """Platform staff/superuser, or an org member holding catalog:edit."""

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        if user.is_staff or user.is_superuser:
            return True
        org = getattr(request, 'org', None)
        return org is not None and user.has_org_permission(org, 'catalog:edit')


class ContentTargetWriteSerializer(serializers.Serializer):
    target_questions = serializers.IntegerField(min_value=0, max_value=MAX_TARGET, required=False)
    target_variants = serializers.IntegerField(min_value=0, max_value=MAX_TARGET, required=False)

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError('target_questions yoki target_variants kerak.')
        return attrs


class ContentPlanView(APIView):
    permission_classes = [IsAuthenticated, IsContentAdmin]

    def get(self, request):
        status = request.query_params.get('status')
        if status and status not in plan.STATUSES:
            raise ValidationError({'status': f'one of {", ".join(plan.STATUSES)}'})
        data = plan.build_plan(
            getattr(request, 'org', None),
            status=status,
            search=request.query_params.get('q', '').strip() or None,
        )
        return Response(data)


class ContentTargetView(APIView):
    permission_classes = [IsAuthenticated, IsContentAdmin]

    def patch(self, request, subject_id):
        org = getattr(request, 'org', None)
        if org is None:
            raise ValidationError("Maqsadni o'zgartirish uchun tashkilot kontekstini tanlang.")
        subject = get_object_or_404(
            Subject.objects.filter(is_active=True).filter(
                Q(organization__isnull=True) | Q(organization=org)
            ),
            pk=subject_id,
        )
        ser = ContentTargetWriteSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        target, _ = ContentTarget.objects.get_or_create(
            organization=org,
            subject=subject,
            defaults={
                'target_questions': plan.DEFAULT_TARGET_QUESTIONS,
                'target_variants': plan.DEFAULT_TARGET_VARIANTS,
            },
        )
        for field, value in ser.validated_data.items():
            setattr(target, field, value)
        target.save()
        row = next(r for r in plan.build_plan(org)['results'] if r['subject_id'] == subject.id)
        return Response(row)
