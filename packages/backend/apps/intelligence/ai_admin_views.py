"""Platform-staff API for AI provider configuration.

  GET/POST          /ai-providers/
  GET/PATCH/DELETE  /ai-providers/<id>/
  POST              /ai-providers/<id>/test/    connection test

The API key is write-only: responses carry only `api_key_last4`. Sending `api_key: ""`
clears it; omitting it on PATCH keeps the stored (encrypted) key.
"""

from django.shortcuts import get_object_or_404
from rest_framework import generics, serializers
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.catalog.models import AIProviderConfig

from .llm import PROFILE_USE_FOR, USE_FOR_PROFILE, test_connection

KEY_REQUIRED = {
    AIProviderConfig.Provider.CLAUDE,
    AIProviderConfig.Provider.OPENAI,
    AIProviderConfig.Provider.GEMINI,
}


class IsPlatformStaff(BasePermission):
    """Platform level only: org owners/managers must not configure the platform AI."""

    def has_permission(self, request, view):
        user = request.user
        return bool(user.is_authenticated and (user.is_staff or user.is_superuser))


class ProfileField(serializers.ChoiceField):
    """API profile name ('default'|'writing'|...) <-> AIProviderConfig.use_for."""

    def __init__(self, **kwargs):
        super().__init__(choices=list(PROFILE_USE_FOR), **kwargs)

    def to_representation(self, value):
        return USE_FOR_PROFILE.get(value, value)

    def to_internal_value(self, data):
        return PROFILE_USE_FOR[super().to_internal_value(data)].value


class AIProviderConfigSerializer(serializers.ModelSerializer):
    profile = ProfileField(source='use_for', default='default')
    api_key = serializers.CharField(
        write_only=True, required=False, allow_blank=True, max_length=500, trim_whitespace=True
    )
    api_key_last4 = serializers.SerializerMethodField()

    class Meta:
        model = AIProviderConfig
        fields = [
            'id',
            'name',
            'provider',
            'model',
            'base_url',
            'api_key',
            'api_key_last4',
            'extra_options',
            'profile',
            'priority',
            'is_active',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def get_api_key_last4(self, obj) -> str:
        return obj.api_key_hint

    def validate_base_url(self, value):
        if value and not value.startswith(('http://', 'https://')):
            raise serializers.ValidationError('Must start with http:// or https://')
        return value

    def validate_extra_options(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError('Must be an object.')
        purpose = value.get('purpose', 'llm')
        if purpose not in ('llm', 'stt'):
            raise serializers.ValidationError("purpose must be 'llm' or 'stt'.")
        return value

    def validate(self, attrs):
        current = self.instance
        provider = attrs.get('provider', current.provider if current else None)
        options = attrs.get('extra_options', current.extra_options if current else {})
        if (options or {}).get('purpose') == 'stt' and provider != AIProviderConfig.Provider.OPENAI:
            raise serializers.ValidationError(
                {
                    'extra_options': "Speech-to-text supports the OpenAI-compatible provider ('openai')."
                }
            )
        has_key = (
            bool(attrs['api_key']) if 'api_key' in attrs else bool(current and current.api_key)
        )
        if provider in KEY_REQUIRED and not has_key:
            raise serializers.ValidationError({'api_key': 'Required for this provider.'})
        return attrs


class ProviderListCreateView(generics.ListCreateAPIView):
    permission_classes = [IsPlatformStaff]
    serializer_class = AIProviderConfigSerializer
    queryset = AIProviderConfig.objects.order_by('priority', 'id')


class ProviderDetailView(generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [IsPlatformStaff]
    serializer_class = AIProviderConfigSerializer
    queryset = AIProviderConfig.objects.all()
    http_method_names = ['get', 'patch', 'delete', 'head', 'options']


class ProviderTestView(APIView):
    permission_classes = [IsPlatformStaff]

    def post(self, request, pk):
        config = get_object_or_404(AIProviderConfig, pk=pk)
        return Response(test_connection(config))
