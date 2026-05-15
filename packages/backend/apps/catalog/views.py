from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.cache import cache_control
from django.views.decorators.http import require_GET, require_POST

from .models import ImportBatch, Question, QuestionDraft, QuestionVersion, Subject, Topic
from .services import watermark as wm
from .services.parsers import _validate


def _get_batch(batch_id, request):
    """Tenant-aware batch olish — boshqa org ning batch'ini ko'rish imkonsiz."""
    qs = (
        ImportBatch.global_objects
        if hasattr(ImportBatch, 'global_objects')
        else ImportBatch.objects
    )
    batch = get_object_or_404(qs, id=batch_id)
    # Tenant chechi: request.org middleware tomonidan o'rnatiladi
    org = getattr(request, 'org', None)
    if org and batch.organization_id != org.id:
        from django.http import Http404

        raise Http404
    return batch


def _draft_counts(batch):
    all_drafts = batch.drafts.all()
    total = all_drafts.count()
    invalid = all_drafts.filter(is_valid=False, is_published=False).count()
    published = all_drafts.filter(is_published=True).count()
    pending = total - published
    return {'total': total, 'invalid': invalid, 'published': published, 'pending': pending}


@login_required
def review_dashboard(request, batch_id):
    batch = _get_batch(batch_id, request)
    counts = _draft_counts(batch)
    subjects = Subject.objects.filter(is_active=True).order_by('name')
    return render(
        request,
        'catalog/review_dashboard.html',
        {
            'batch': batch,
            'counts': counts,
            'subjects': subjects,
        },
    )


@login_required
def draft_list_htmx(request, batch_id):
    batch = _get_batch(batch_id, request)
    status = request.GET.get('status', 'all')
    search = request.GET.get('q', '').strip()

    drafts = batch.drafts.all()

    if status == 'pending':
        drafts = drafts.filter(is_published=False)
    elif status == 'invalid':
        drafts = drafts.filter(is_valid=False, is_published=False)
    elif status == 'published':
        drafts = drafts.filter(is_published=True)

    if search:
        drafts = drafts.filter(data__text__icontains=search)

    counts = _draft_counts(batch)
    filter_tabs = [
        ('all', 'Hammasi', counts['total']),
        ('pending', 'Kutmoqda', counts['pending']),
        ('invalid', 'Xato', counts['invalid']),
        ('published', 'Nashr', counts['published']),
    ]

    import json

    draft_ids_json = json.dumps([str(d.id) for d in drafts])

    return render(
        request,
        'catalog/partials/draft_list.html',
        {
            'drafts': drafts,
            'batch': batch,
            'status': status,
            'search': search,
            'counts': counts,
            'filter_tabs': filter_tabs,
            'draft_ids_json': draft_ids_json,
        },
    )


@login_required
def draft_detail_htmx(request, draft_id):
    draft = get_object_or_404(QuestionDraft, id=draft_id)
    batch = draft.batch
    org = getattr(request, 'org', None)
    if org and batch.organization_id != org.id:
        from django.http import Http404

        raise Http404
    subjects = Subject.objects.filter(is_active=True).order_by('name')
    topics = Topic.objects.filter(subject=draft.subject).order_by('name') if draft.subject else []
    return render(
        request,
        'catalog/partials/draft_detail.html',
        {
            'draft': draft,
            'subjects': subjects,
            'topics': topics,
        },
    )


@login_required
@require_POST
def draft_autosave_htmx(request, draft_id):
    draft = get_object_or_404(QuestionDraft, id=draft_id)
    org = getattr(request, 'org', None)
    if org and draft.batch.organization_id != org.id:
        return HttpResponse(status=403)

    data = dict(draft.data)  # shallow copy

    # Savol matni
    text = request.POST.get('text')
    if text is not None:
        data['text'] = text.strip()

    # Subject / Topic
    subject_id = request.POST.get('subject_id')
    topic_id = request.POST.get('topic_id')
    if subject_id:
        try:
            draft.subject = Subject.objects.get(id=subject_id)
        except Subject.DoesNotExist:
            pass
    if topic_id:
        try:
            draft.topic = Topic.objects.get(id=topic_id)
        except Topic.DoesNotExist:
            pass

    # Variant matnlari
    options = data.get('options', [])
    for i, opt in enumerate(options):
        opt_text = request.POST.get(f'option_{i}')
        if opt_text is not None:
            options[i] = {**opt, 'text': opt_text.strip()}

    # To'g'ri javob toggle
    correct_idx = request.POST.get('correct_idx')
    if correct_idx is not None:
        try:
            idx = int(correct_idx)
            for i, opt in enumerate(options):
                options[i] = {**opt, 'is_correct': (i == idx)}
        except (ValueError, TypeError):
            pass

    data['options'] = options
    draft.data = data
    draft.is_valid = _validate(data)
    draft.save()

    # OOB ile chap paneldagi badge ni ham yangilash
    badge_html = _status_badge_html(draft)
    return HttpResponse(
        f'<span id="save-{draft_id}" class="text-green-600 text-xs font-medium">✓ Saqlandi</span>'
        f'<span hx-swap-oob="true" id="badge-{draft_id}">{badge_html}</span>'
    )


@login_required
@require_POST
def draft_bulk_action_htmx(request, batch_id):
    batch = _get_batch(batch_id, request)
    action = request.POST.get('action')
    draft_ids = request.POST.getlist('draft_ids')

    if not draft_ids:
        return _toast('Hech qanday savol tanlanmadi.', kind='warning')

    drafts = QuestionDraft.objects.filter(id__in=draft_ids, batch=batch)

    if action == 'delete':
        count = drafts.count()
        drafts.delete()
        return _toast(f"{count} ta savol o'chirildi.", kind='info', reload=True)

    if action == 'publish':
        published, skipped = 0, 0
        for draft in drafts.select_related('subject', 'topic'):
            if draft.is_published:
                skipped += 1
                continue
            if not draft.is_valid:
                skipped += 1
                continue
            if not draft.subject:
                skipped += 1
                continue

            q = Question.objects.create(
                organization=batch.organization,
                subject=draft.subject,
                topic=draft.topic,
                type=draft.type or Question.Type.SINGLE_CHOICE,
            )
            QuestionVersion.objects.create(
                question=q,
                version_number=1,
                content={'text': draft.data.get('text', ''), 'media': draft.data.get('media', [])},
                options=draft.data.get('options', []),
                created_by=request.user if request.user.is_authenticated else None,
            )
            draft.is_published = True
            draft.published_question = q
            draft.save(update_fields=['is_published', 'published_question'])
            published += 1

        parts = []
        if published:
            parts.append(f'{published} ta savol nashr qilindi')
        if skipped:
            parts.append(f"{skipped} ta o'tkazib yuborildi (yaroqsiz yoki fan tanlanmagan)")
        msg = '. '.join(parts) + '.'
        kind = 'success' if published else 'warning'
        return _toast(msg, kind=kind, reload=True)

    return _toast("Noma'lum amal.", kind='error')


# ── Helpers ──────────────────────────────────────────────────────────────────


def _status_badge_html(draft: QuestionDraft) -> str:
    if draft.is_published:
        return '<span class="px-2 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-700">Nashr</span>'
    if not draft.is_valid:
        return '<span class="px-2 py-0.5 rounded-full text-xs font-medium bg-red-100 text-red-600">Xato</span>'
    return '<span class="px-2 py-0.5 rounded-full text-xs font-medium bg-yellow-100 text-yellow-700">Qoralama</span>'


def _toast(message: str, kind: str = 'info', reload: bool = False) -> HttpResponse:
    """
    HTMX OOB swap orqali #toast-container ga toast chiqaradi.
    reload=True bo'lsa chap panel ham yangilanadi.
    """
    colors = {
        'success': 'bg-green-600',
        'error': 'bg-red-600',
        'warning': 'bg-amber-500',
        'info': 'bg-blue-600',
    }
    bg = colors.get(kind, 'bg-gray-700')
    reload_trigger = (
        (
            '<span hx-swap-oob="true" id="draft-list-reload-trigger" '
            'hx-get="" hx-trigger="load" hx-target="#draft-list-panel" '
            'hx-swap="innerHTML"></span>'
        )
        if reload
        else ''
    )

    toast_html = f"""
<div id="toast-container" hx-swap-oob="true">
  <div x-data="{{show: true}}" x-show="show" x-init="setTimeout(()=>show=false, 3500)"
       x-transition:leave="transition ease-in duration-300"
       x-transition:leave-start="opacity-100 translate-y-0"
       x-transition:leave-end="opacity-0 translate-y-2"
       class="{bg} text-white text-sm font-medium px-5 py-3 rounded-xl shadow-lg flex items-center gap-3 pointer-events-auto">
    <span>{message}</span>
    <button @click="show=false" class="ml-auto opacity-70 hover:opacity-100 text-lg leading-none">&times;</button>
  </div>
</div>
{reload_trigger}
"""
    resp = HttpResponse(toast_html)
    if reload:
        resp['HX-Trigger'] = 'draftListReload'
    return resp


# ── Watermark endpoint ────────────────────────────────────────


@login_required
@require_GET
@cache_control(private=True, max_age=3600)
def watermark_png(request):
    """
    Authenticated user uchun shaxsiy watermark PNG.
    URL: /catalog/wm.png
    Cache: 1 soat (private, browser-side).
    """
    org = getattr(request, 'org', None)
    data = wm.for_user(request.user, org)
    if data is None:
        return HttpResponse(status=204)

    png = wm.render_png(data)
    return HttpResponse(png, content_type='image/png')
