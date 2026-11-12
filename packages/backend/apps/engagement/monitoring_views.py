"""Live exam monitoring: GET /api/v1/engagement/monitoring/live-attempts/

Query: ?exam_id=<uuid>&min_strikes=<n>&limit=<1..200>. Needs exam:edit (teacher / admin).
"""

from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from . import monitoring_service as svc
from .permissions import CanEditExams


def _int(request, name, default, lo, hi):
    raw = request.query_params.get(name)
    if raw in (None, ''):
        return default
    try:
        n = int(raw)
    except ValueError:
        raise ValidationError({name: 'Must be an integer.'}) from None
    if not lo <= n <= hi:
        raise ValidationError({name: f'Must be between {lo} and {hi}.'})
    return n


class LiveAttemptsView(APIView):
    permission_classes = [CanEditExams]

    def get(self, request):
        exam_id = request.query_params.get('exam_id') or None
        if exam_id:
            import uuid

            try:
                uuid.UUID(exam_id)
            except ValueError:
                raise ValidationError({'exam_id': 'Must be a UUID.'}) from None
        return Response(
            svc.live_attempts(
                exam_id=exam_id,
                min_strikes=_int(request, 'min_strikes', 0, 0, 100),
                limit=_int(request, 'limit', svc.DEFAULT_LIMIT, 1, svc.MAX_LIMIT),
            )
        )
