"""Group / member / lesson REST API (tenant-scoped, mounted at /api/v1/organizations/)."""

from django.shortcuts import get_object_or_404
from django.utils.dateparse import parse_datetime
from rest_framework import generics, status
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.views import APIView

from . import group_services as svc
from .group_models import GroupMember, GroupRole, Lesson
from .group_serializers import (
    AddMemberSerializer,
    BulkMembersSerializer,
    GroupSerializer,
    LessonSerializer,
    MemberSerializer,
)


class HasOrgPermission(BasePermission):
    """``view.perm_map = {'GET': [any-of codes], ...}``; the user needs one code of the
    method's list in request.org. Methods absent from the map are denied."""

    def has_permission(self, request, view):
        user = request.user
        org = getattr(request, 'org', None)
        if not (user and user.is_authenticated) or org is None:
            return False
        codes = view.perm_map.get(request.method, [])
        return any(user.has_org_permission(org, c) for c in codes)


class OrgContextMixin:
    permission_classes = [HasOrgPermission]

    @property
    def org(self):
        return self.request.org

    def get_serializer_context(self):
        return {**super().get_serializer_context(), 'org': self.org}


def _group_or_404(request, pk):
    # visible_groups() is tenant-scoped AND membership-scoped: foreign ids are 404.
    return get_object_or_404(svc.visible_groups(request.user, request.org), pk=pk)


# ── Groups ──────────────────────────────────────────────────────────────────


class GroupListCreateView(OrgContextMixin, generics.ListCreateAPIView):
    serializer_class = GroupSerializer
    perm_map = {
        'GET': ['groups:view', 'groups:view_own'],
        'POST': ['groups:create'],
    }

    def get_queryset(self):
        qs = svc.visible_groups(self.request.user, self.org).prefetch_related('members__user')
        archived = self.request.query_params.get('archived') in ('1', 'true')
        return qs.filter(is_archived=archived)

    def perform_create(self, serializer):
        teachers = serializer.validated_data.pop('teacher_usernames', [])
        group = serializer.save(organization=self.org, created_by=self.request.user)
        if teachers:
            report = svc.add_members(
                group,
                svc.normalize_usernames(teachers),
                role=GroupRole.TEACHER,
                added_by=self.request.user,
                all_or_nothing=True,
            )
            if not report['committed']:
                # roll the group back so a bad teacher list does not leave a half-made group
                group.delete()
                raise ValidationError({'teacher_usernames': report['rows']})


class GroupDetailView(OrgContextMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = GroupSerializer
    http_method_names = ['get', 'patch', 'delete', 'head', 'options']
    perm_map = {
        'GET': ['groups:view', 'groups:view_own'],
        'PATCH': ['groups:edit'],
        'DELETE': ['groups:delete'],
    }

    def get_queryset(self):
        return svc.visible_groups(self.request.user, self.org).prefetch_related('members__user')

    def perform_update(self, serializer):
        serializer.validated_data.pop('teacher_usernames', None)
        serializer.save()


# ── Members ─────────────────────────────────────────────────────────────────


class MembersMixin(OrgContextMixin):
    def get_group(self, pk, *, for_read=False):
        group = _group_or_404(self.request, pk)
        if for_read:
            # students must not enumerate classmates: wide readers and the group's
            # teachers only
            wide = self.request.user.has_org_permission(self.org, svc.PERM_VIEW_ALL)
            if not (wide or svc.is_group_teacher(self.request.user, group)):
                raise NotFound()
        return group


class MemberListCreateView(MembersMixin, APIView):
    perm_map = {
        'GET': ['groups:view', 'groups:view_own'],
        'POST': ['groups:members'],
    }

    def get(self, request, pk):
        group = self.get_group(pk, for_read=True)
        qs = GroupMember.objects.filter(group=group).select_related('user')
        if role := request.query_params.get('role'):
            qs = qs.filter(role=role)
        return Response(MemberSerializer(qs, many=True).data)

    def post(self, request, pk):
        group = self.get_group(pk)
        ser = AddMemberSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        report = svc.add_members(
            group,
            [(1, ser.validated_data['username'].strip())],
            role=ser.validated_data['role'],
            added_by=request.user,
            all_or_nothing=True,
        )
        row = report['rows'][0]
        if row['status'] == 'error':
            raise ValidationError({'username': row['error']})
        if row['status'] == 'already_member':
            return Response({'detail': "Allaqachon guruh a'zosi."}, status=status.HTTP_409_CONFLICT)
        member = GroupMember.objects.select_related('user').get(
            group=group, user__username__iexact=row['username']
        )
        return Response(MemberSerializer(member).data, status=status.HTTP_201_CREATED)


class MemberDetailView(MembersMixin, APIView):
    perm_map = {'DELETE': ['groups:members']}

    def delete(self, request, pk, member_id):
        group = self.get_group(pk)
        member = get_object_or_404(GroupMember.objects.filter(group=group), pk=member_id)
        member.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


def _bulk_response(report):
    code = status.HTTP_200_OK if report['committed'] else status.HTTP_422_UNPROCESSABLE_ENTITY
    return Response(report, status=code)


class MemberBulkView(MembersMixin, APIView):
    perm_map = {'POST': ['groups:members']}

    def post(self, request, pk):
        group = self.get_group(pk)
        ser = BulkMembersSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        report = svc.add_members(
            group,
            svc.normalize_usernames(d['usernames']),
            role=d['role'],
            added_by=request.user,
            all_or_nothing=d['all_or_nothing'],
        )
        return _bulk_response(report)


class MemberCsvImportView(MembersMixin, APIView):
    parser_classes = [MultiPartParser, FormParser]
    perm_map = {'POST': ['groups:members']}

    def post(self, request, pk):
        group = self.get_group(pk)
        upload = request.FILES.get('file')
        if upload is None:
            raise ValidationError({'file': 'CSV fayl yuborilmadi.'})
        role = request.data.get('role', GroupRole.STUDENT)
        if role not in GroupRole.values:
            raise ValidationError({'role': 'student yoki teacher.'})
        all_or_nothing = str(request.data.get('all_or_nothing', '')).lower() in ('1', 'true', 'on')
        try:
            entries = svc.parse_username_csv(upload)
        except svc.CsvFileError as exc:
            raise ValidationError({'file': str(exc)}) from exc
        report = svc.add_members(
            group, entries, role=role, added_by=request.user, all_or_nothing=all_or_nothing
        )
        return _bulk_response(report)


# ── Lessons ─────────────────────────────────────────────────────────────────

LESSON_READ = ['groups:view', 'groups:view_own', 'lessons:view_own', 'lessons:view']


class LessonQuerysetMixin(OrgContextMixin):
    def get_queryset(self):
        groups = svc.visible_groups(self.request.user, self.org)
        return Lesson.objects.filter(group__in=groups).select_related('exam', 'group')


class LessonListCreateView(LessonQuerysetMixin, generics.ListCreateAPIView):
    serializer_class = LessonSerializer
    perm_map = {'GET': LESSON_READ, 'POST': ['lessons:create']}

    def get_queryset(self):
        qs = super().get_queryset()
        p = self.request.query_params
        if group := p.get('group'):
            qs = qs.filter(group_id=group)
        for key, lookup in (('from', 'scheduled_at__gte'), ('to', 'scheduled_at__lte')):
            if p.get(key):
                dt = parse_datetime(p[key])
                if dt is None:
                    raise ValidationError({key: 'ISO 8601 datetime kerak.'})
                qs = qs.filter(**{lookup: dt})
        return qs

    def perform_create(self, serializer):
        group = serializer.validated_data['group']
        if not svc.can_manage_lessons_of(self.request.user, self.org, group, 'create'):
            raise PermissionDenied("Bu guruhga dars qo'shish huquqingiz yo'q.")
        serializer.save(organization=self.org, created_by=self.request.user)


class LessonDetailView(LessonQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = LessonSerializer
    http_method_names = ['get', 'patch', 'delete', 'head', 'options']
    perm_map = {'GET': LESSON_READ, 'PATCH': ['lessons:edit'], 'DELETE': ['lessons:delete']}

    def perform_update(self, serializer):
        self._require(serializer.instance, 'edit')
        serializer.save()

    def perform_destroy(self, instance):
        self._require(instance, 'delete')
        instance.delete()

    def _require(self, lesson, action):
        if not svc.can_manage_lessons_of(self.request.user, self.org, lesson.group, action):
            raise PermissionDenied("Bu darsni o'zgartirish huquqingiz yo'q.")
