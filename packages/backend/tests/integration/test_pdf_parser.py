"""PdfParserService: PDF bulk import -> QuestionDraft rows."""

import pytest
from django.core.files.base import ContentFile

from apps.catalog.models import ImportBatch, QuestionDraft
from apps.catalog.services.ai_parser import AIParserService
from apps.catalog.services.parsers import ParserDispatcher, PdfParserService
from core.tenant import tenant_context, unscoped_context


def _make_pdf(pages: list[list[str]]) -> bytes:
    """Build a real text PDF (Helvetica, one line per entry) without extra dependencies.

    Each line is its own text object at a lower y position, so pypdf extracts
    one line per entry, the same way it reads exported question sheets.
    """

    def escape(text: str) -> str:
        return text.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')

    objects = {
        1: b'<< /Type /Catalog /Pages 2 0 R >>',
        3: b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
    }
    kids = []
    next_id = 4
    for lines in pages:
        ops = ''.join(
            f'BT /F1 12 Tf 72 {760 - 20 * i} Td ({escape(line)}) Tj ET\n'
            for i, line in enumerate(lines)
        ).encode('latin-1')
        objects[next_id] = b'<< /Length %d >>\nstream\n' % len(ops) + ops + b'endstream'
        objects[next_id + 1] = (
            b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] '
            b'/Resources << /Font << /F1 3 0 R >> >> /Contents %d 0 R >>' % next_id
        )
        kids.append(next_id + 1)
        next_id += 2
    kid_refs = ' '.join(f'{k} 0 R' for k in kids).encode()
    objects[2] = b'<< /Type /Pages /Kids [%s] /Count %d >>' % (kid_refs, len(kids))

    out = bytearray(b'%PDF-1.4\n')
    offsets = {}
    for oid in sorted(objects):
        offsets[oid] = len(out)
        out += b'%d 0 obj\n' % oid + objects[oid] + b'\nendobj\n'
    xref_at = len(out)
    out += b'xref\n0 %d\n0000000000 65535 f \n' % (len(objects) + 1)
    out += b''.join(b'%010d 00000 n \n' % offsets[oid] for oid in sorted(objects))
    out += b'trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n' % (
        len(objects) + 1,
        xref_at,
    )
    return bytes(out)


@pytest.fixture
def media_root(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    return tmp_path


@pytest.fixture
def make_batch(db, org, media_root):
    def _make(content: bytes, file_type: str = 'pdf') -> ImportBatch:
        with tenant_context(org):
            batch = ImportBatch.objects.create(organization=org, file_type=file_type)
            batch.file.save('questions.pdf', ContentFile(content), save=True)
        return batch

    return _make


def _parse(org, batch: ImportBatch) -> int:
    with tenant_context(org):
        count = PdfParserService(batch).parse()
        batch.refresh_from_db()
    return count


def _drafts_by_text(batch: ImportBatch) -> dict[str, QuestionDraft]:
    return {d.data['text']: d for d in _drafts().filter(batch=batch)}


@pytest.mark.integration
class TestPdfParserService:
    def test_parses_questions_options_and_starred_answer(self, org, make_batch, settings):
        settings.ENABLE_AI_PARSER = False
        batch = make_batch(
            _make_pdf(
                [
                    [
                        '1. 2 + 2 = ?',
                        'A) 3',
                        '*B) 4',
                        'C) 5',
                        '2) Poytaxt qaysi shahar?',
                        '*A. Toshkent',
                        'B. Samarqand',
                    ]
                ]
            )
        )

        assert _parse(org, batch) == 2
        assert batch.status == ImportBatch.Status.COMPLETED
        assert batch.total_questions == 2

        drafts = _drafts_by_text(batch)
        assert set(drafts) == {'2 + 2 = ?', 'Poytaxt qaysi shahar?'}
        assert drafts['2 + 2 = ?'].data['options'] == [
            {'label': 'A', 'text': '3', 'is_correct': False},
            {'label': 'B', 'text': '4', 'is_correct': True},
            {'label': 'C', 'text': '5', 'is_correct': False},
        ]
        assert [o['is_correct'] for o in drafts['Poytaxt qaysi shahar?'].data['options']] == [
            True,
            False,
        ]
        assert all(d.is_valid for d in drafts.values())

    def test_question_text_continues_across_lines_and_pages(self, org, make_batch, settings):
        settings.ENABLE_AI_PARSER = False
        batch = make_batch(
            _make_pdf(
                [
                    ['1. Uzun savol', 'birinchi sahifadagi davomi'],
                    ['ikkinchi sahifadagi qism', '*A) ha', "B) yo'q"],
                ]
            )
        )

        assert _parse(org, batch) == 1
        draft = _drafts().get(batch=batch)
        assert draft.data['text'] == (
            'Uzun savol\nbirinchi sahifadagi davomi\nikkinchi sahifadagi qism'
        )
        assert [o['label'] for o in draft.data['options']] == ['A', 'B']
        assert draft.is_valid

    def test_questions_without_correct_answer_or_options_are_invalid(
        self, org, make_batch, settings
    ):
        settings.ENABLE_AI_PARSER = False
        batch = make_batch(
            _make_pdf(
                [['1. Javobsiz savol', 'A) bir', 'B) ikki', '2. Bitta variant', '*A) yolgiz']]
            )
        )

        assert _parse(org, batch) == 2
        drafts = _drafts_by_text(batch)
        assert drafts['Javobsiz savol'].is_valid is False
        assert drafts['Bitta variant'].is_valid is False

    def test_preamble_is_skipped_without_ai_when_ai_disabled(
        self, org, make_batch, settings, monkeypatch
    ):
        settings.ENABLE_AI_PARSER = False

        def fail_if_called(self, text):
            raise AssertionError(f'AI parser must not be called: {text!r}')

        monkeypatch.setattr(AIParserService, 'parse_text_block', fail_if_called)
        batch = make_batch(_make_pdf([['Matematika testi', '1. 3 * 3 = ?', '*A) 9', 'B) 6']]))

        assert _parse(org, batch) == 1
        assert set(_drafts_by_text(batch)) == {'3 * 3 = ?'}

    def test_preamble_goes_to_ai_when_enabled_and_errors_are_dropped(
        self, org, make_batch, settings, monkeypatch
    ):
        settings.ENABLE_AI_PARSER = True
        calls = []

        def fake_ai(self, text):
            calls.append(text)
            if text.startswith('Tuzilmagan'):
                return {
                    'text': 'AI ajratgan savol',
                    'options': [
                        {'label': 'A', 'text': 'x', 'is_correct': True},
                        {'label': 'B', 'text': 'y', 'is_correct': False},
                    ],
                }
            return {'error': 'provider unavailable', 'raw_text': text}

        monkeypatch.setattr(AIParserService, 'parse_text_block', fake_ai)
        batch = make_batch(
            _make_pdf([['Tuzilmagan blok', 'Shovqin qatori', '1. Oddiy savol', '*A) ha', 'B) yoq']])
        )

        assert _parse(org, batch) == 2
        assert calls == ['Tuzilmagan blok', 'Shovqin qatori']
        assert set(_drafts_by_text(batch)) == {'AI ajratgan savol', 'Oddiy savol'}

    def test_unreadable_file_marks_batch_failed(self, org, make_batch, settings):
        settings.ENABLE_AI_PARSER = False
        batch = make_batch(b'this is not a pdf')

        assert _parse(org, batch) == 0
        assert batch.status == ImportBatch.Status.FAILED
        assert batch.error_log and batch.error_log.get('error')
        assert not _drafts().filter(batch=batch)

    def test_pdf_without_text_completes_with_no_questions(self, org, make_batch, settings):
        settings.ENABLE_AI_PARSER = False
        batch = make_batch(_make_pdf([[], []]))

        assert _parse(org, batch) == 0
        assert batch.status == ImportBatch.Status.COMPLETED
        assert not _drafts().filter(batch=batch)

    def test_dispatcher_routes_pdf_regardless_of_case(self, org, make_batch, settings):
        settings.ENABLE_AI_PARSER = False
        batch = make_batch(_make_pdf([['1. Savol', '*A) ha', 'B) yoq']]), file_type='PDF')

        assert isinstance(ParserDispatcher.get_parser(batch), PdfParserService)
        with tenant_context(org):
            assert ParserDispatcher.parse(batch) == 1


def _drafts():
    """Draft rows for assertions, read past RLS (the parser runs in the batch's org)."""
    return _UnscopedDrafts()


class _UnscopedDrafts:
    def __getattr__(self, name):
        method = getattr(QuestionDraft.objects, name)

        def call(*args, **kwargs):
            with unscoped_context():
                result = method(*args, **kwargs)
                return list(result) if hasattr(result, '_fetch_all') else result

        return call
