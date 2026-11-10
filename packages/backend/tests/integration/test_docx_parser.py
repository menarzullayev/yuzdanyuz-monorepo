"""DocxParserService: Word bulk import -> QuestionDraft rows."""

import io
import struct
import zlib

import docx
import pytest
from django.core.files.base import ContentFile

from apps.catalog.models import ImportBatch, QuestionDraft
from apps.catalog.services.ai_parser import AIParserService
from apps.catalog.services.parsers import DocxParserService, ParserDispatcher
from core.tenant import tenant_context


def _png_1x1() -> bytes:
    """A valid 1x1 white PNG, so tests can embed a real picture without fixture files."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
        )

    header = struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0)
    pixels = zlib.compress(b'\x00\xff\xff\xff')
    return (
        b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', header) + chunk(b'IDAT', pixels) + chunk(b'IEND', b'')
    )


def _make_docx(paragraphs: list) -> bytes:
    """Each entry is a string, or a list of (text, bold) runs, or {'picture': text}."""
    document = docx.Document()
    for entry in paragraphs:
        if isinstance(entry, str):
            document.add_paragraph(entry)
        elif isinstance(entry, dict):
            para = document.add_paragraph(entry['picture'])
            para.add_run().add_picture(io.BytesIO(_png_1x1()))
        else:
            para = document.add_paragraph()
            for text, bold in entry:
                para.add_run(text).bold = bold
    out = io.BytesIO()
    document.save(out)
    return out.getvalue()


@pytest.fixture
def media_root(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    return tmp_path


@pytest.fixture
def make_batch(db, org, media_root):
    def _make(content: bytes, file_type: str = 'docx') -> ImportBatch:
        with tenant_context(org):
            batch = ImportBatch.objects.create(organization=org, file_type=file_type)
            batch.file.save('questions.docx', ContentFile(content), save=True)
        return batch

    return _make


def _parse(org, batch: ImportBatch) -> int:
    with tenant_context(org):
        count = DocxParserService(batch).parse()
        batch.refresh_from_db()
    return count


def _drafts_by_text(batch: ImportBatch) -> dict[str, QuestionDraft]:
    return {d.data['text']: d for d in QuestionDraft.objects.filter(batch=batch)}


@pytest.mark.integration
class TestDocxParserService:
    def test_text_only_document_has_no_media(self, org, make_batch, settings):
        """Regression: every run used to count as an image ('drawing' is in the run XML namespaces)."""
        settings.ENABLE_AI_PARSER = False
        batch = make_batch(_make_docx(['1. 2 + 2 = ?', 'A) 3', '*B) 4', 'C) 5']))

        assert _parse(org, batch) == 1
        draft = QuestionDraft.objects.get(batch=batch)
        assert draft.data['media'] == []
        assert all('media' not in option for option in draft.data['options'])

    def test_real_pictures_are_counted_where_they_are(self, org, make_batch, settings):
        settings.ENABLE_AI_PARSER = False
        batch = make_batch(
            _make_docx(
                [
                    {'picture': '1. Rasmdagi shakl nechta burchakka ega?'},
                    {'picture': 'A) uchta'},
                    "*B) to'rtta",
                    {'picture': ''},
                ]
            )
        )

        assert _parse(org, batch) == 1
        draft = QuestionDraft.objects.get(batch=batch)
        # question picture + the picture-only paragraph after the options
        assert len(draft.data['media']) == 2
        assert all(
            m == {'type': 'image', 'status': 'extracted_from_docx'} for m in draft.data['media']
        )
        option_a, option_b = draft.data['options']
        assert len(option_a['media']) == 1
        assert 'media' not in option_b

    def test_star_and_bold_mark_the_correct_option(self, org, make_batch, settings):
        settings.ENABLE_AI_PARSER = False
        batch = make_batch(
            _make_docx(
                [
                    '1. Poytaxt?',
                    [('A) Toshkent', True)],
                    'B) Samarqand',
                    '2. 3 * 3 = ?',
                    [('A) ', False), ('9', True)],
                    '*B) 6',
                ]
            )
        )

        assert _parse(org, batch) == 2
        drafts = _drafts_by_text(batch)
        assert [o['is_correct'] for o in drafts['Poytaxt?'].data['options']] == [True, False]
        # only part of the option is bold -> not marked; the starred one is
        assert [o['is_correct'] for o in drafts['3 * 3 = ?'].data['options']] == [False, True]
        assert all(d.is_valid for d in drafts.values())

    def test_question_text_continues_and_invalid_questions_are_flagged(
        self, org, make_batch, settings
    ):
        settings.ENABLE_AI_PARSER = False
        batch = make_batch(
            _make_docx(
                ['1. Uzun savol', 'davomi', 'A) bir', 'B) ikki', '2) Bitta variant', '*A) yolgiz']
            )
        )

        assert _parse(org, batch) == 2
        drafts = _drafts_by_text(batch)
        assert drafts['Uzun savol\ndavomi'].is_valid is False  # no correct answer
        assert drafts['Bitta variant'].is_valid is False  # single option

    def test_preamble_skipped_when_ai_disabled(self, org, make_batch, settings, monkeypatch):
        settings.ENABLE_AI_PARSER = False

        def fail_if_called(self, text):
            raise AssertionError(f'AI parser must not be called: {text!r}')

        monkeypatch.setattr(AIParserService, 'parse_text_block', fail_if_called)
        batch = make_batch(_make_docx(['Matematika testi', '1. Savol', '*A) ha', 'B) yoq']))

        assert _parse(org, batch) == 1
        assert set(_drafts_by_text(batch)) == {'Savol'}

    def test_preamble_goes_to_ai_when_enabled(self, org, make_batch, settings, monkeypatch):
        settings.ENABLE_AI_PARSER = True
        calls = []

        def fake_ai(self, text):
            calls.append(text)
            return {
                'text': 'AI savol',
                'options': [
                    {'label': 'A', 'text': 'x', 'is_correct': True},
                    {'label': 'B', 'text': 'y', 'is_correct': False},
                ],
            }

        monkeypatch.setattr(AIParserService, 'parse_text_block', fake_ai)
        batch = make_batch(_make_docx(['Tuzilmagan blok', '1. Oddiy savol', '*A) ha', 'B) yoq']))

        assert _parse(org, batch) == 2
        assert calls == ['Tuzilmagan blok']
        assert set(_drafts_by_text(batch)) == {'AI savol', 'Oddiy savol'}

    def test_unreadable_file_marks_batch_failed(self, org, make_batch, settings):
        settings.ENABLE_AI_PARSER = False
        batch = make_batch(b'this is not a docx')

        assert _parse(org, batch) == 0
        assert batch.status == ImportBatch.Status.FAILED
        assert batch.error_log and batch.error_log.get('error')
        assert not QuestionDraft.objects.filter(batch=batch).exists()

    def test_dispatcher_routes_docx_regardless_of_case(self, org, make_batch, settings):
        settings.ENABLE_AI_PARSER = False
        batch = make_batch(_make_docx(['1. Savol', '*A) ha', 'B) yoq']), file_type='DOCX')

        assert isinstance(ParserDispatcher.get_parser(batch), DocxParserService)
        with tenant_context(org):
            assert ParserDispatcher.parse(batch) == 1
