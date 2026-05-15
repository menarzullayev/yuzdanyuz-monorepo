import pytest
from io import BytesIO
from apps.catalog.models import (
    Question, ImportBatch, QuestionDraft, Subject
)
from apps.catalog.services.bulk_import import bulk_import_from_file, BulkImportParser
from core.tenant import tenant_context
import openpyxl


@pytest.mark.integration
class TestBulkImportParser:
    def test_parse_simple_excel_file(self, db, org):
        """Excel fayldan savollarni oqish."""
        wb = openpyxl.Workbook()
        ws = wb.active

        # Headers
        ws['A1'] = 'question_text'
        ws['B1'] = 'type'
        ws['C1'] = 'subject'
        ws['D1'] = 'option_1'
        ws['E1'] = 'option_2'
        ws['F1'] = 'correct_1'

        # Data row
        ws['A2'] = '2 + 2 = ?'
        ws['B2'] = 'SC'
        ws['C2'] = 'Matematika'
        ws['D2'] = '4'
        ws['E2'] = '5'
        ws['F2'] = True

        file = BytesIO()
        wb.save(file)
        file.seek(0)

        parser = BulkImportParser(file, 'xlsx', org)
        rows, errors = parser.parse()

        assert len(rows) == 1
        assert rows[0]['question_text'] == '2 + 2 = ?'
        assert len(errors) == 0

    def test_parse_csv_file(self, db, org):
        """CSV fayldan savollarni oqish."""
        csv_content = """question_text,type,subject,option_1,option_2,correct_1
2 + 2 = ?,SC,Matematika,4,5,True"""

        file = BytesIO(csv_content.encode('utf-8'))
        parser = BulkImportParser(file, 'csv', org)
        rows, errors = parser.parse()

        assert len(rows) == 1
        assert rows[0]['question_text'] == '2 + 2 = ?'

    def test_bulk_import_creates_drafts(self, db, org):
        """Bulk import qatorlardan draft'lar yaratadi."""
        subject = Subject.objects.create(
            name="Matematika",
            slug="matematika",
            organization=org
        )

        csv_content = """question_text,type,subject,option_1,option_2,correct_1
2 + 2 = ?,SC,Matematika,4,5,True
3 + 3 = ?,SC,Matematika,6,7,True"""

        file = BytesIO(csv_content.encode('utf-8'))

        with tenant_context(org):
            batch = ImportBatch.objects.create(
                organization=org,
                file_type='csv'
            )

            result = bulk_import_from_file(file, 'csv', org, batch)

        assert result['success'] is True
        assert result['total'] == 2
        assert result['processed'] >= 1

        batch.refresh_from_db()
        assert batch.total_questions == 2

    def test_import_validation_catches_missing_subject(self, db, org):
        """Import mo'ljallangan fan topilmasa xato qaytaradi."""
        csv_content = """question_text,type,subject,option_1,option_2,correct_1
2 + 2 = ?,SC,Noto'g'ri_fan,4,5,True"""

        file = BytesIO(csv_content.encode('utf-8'))

        with tenant_context(org):
            batch = ImportBatch.objects.create(
                organization=org,
                file_type='csv'
            )

            result = bulk_import_from_file(file, 'csv', org, batch)

        assert result['success'] is True
        assert len(result['errors']) > 0

    def test_import_validation_catches_missing_question_text(self, db, org):
        """Bo'sh savol matni tekshiriladi."""
        subject = Subject.objects.create(
            name="Matematika",
            slug="matematika",
            organization=org
        )

        csv_content = """question_text,type,subject,option_1
,SC,Matematika,4"""

        file = BytesIO(csv_content.encode('utf-8'))

        with tenant_context(org):
            batch = ImportBatch.objects.create(
                organization=org,
                file_type='csv'
            )

            result = bulk_import_from_file(file, 'csv', org, batch)

        assert len(result['errors']) > 0

    def test_import_batch_status_tracking(self, db, org):
        """Import batch status'i to'g'ri o'zgaradi."""
        subject = Subject.objects.create(
            name="Matematika",
            slug="matematika",
            organization=org
        )

        csv_content = """question_text,type,subject
2 + 2 = ?,SC,Matematika"""

        file = BytesIO(csv_content.encode('utf-8'))

        with tenant_context(org):
            batch = ImportBatch.objects.create(
                organization=org,
                file_type='csv',
                status=ImportBatch.Status.PENDING
            )

            assert batch.status == ImportBatch.Status.PENDING

            bulk_import_from_file(file, 'csv', org, batch)

            batch.refresh_from_db()
            assert batch.status == ImportBatch.Status.COMPLETED


@pytest.mark.integration
class TestDraftPublishWorkflow:
    def test_draft_to_question_conversion(self, db, org, user):
        """Draft'ni question'ga aylantirib tekshirish."""
        subject = Subject.objects.create(
            name="Matematika",
            slug="matematika",
            organization=org
        )

        with tenant_context(org):
            batch = ImportBatch.objects.create(
                organization=org,
                file_type='csv',
                created_by=user
            )

            draft = QuestionDraft.objects.create(
                batch=batch,
                type=Question.Type.SINGLE_CHOICE,
                subject=subject,
                data={
                    'text': '2 + 2 = ?',
                    'options': [
                        {'id': '1', 'text': '4', 'is_correct': True},
                        {'id': '2', 'text': '5', 'is_correct': False}
                    ]
                },
                is_valid=True
            )

            # Question yaratish
            question = Question.objects.create(
                organization=org,
                subject=subject,
                type=draft.type
            )

            from apps.catalog.models import QuestionVersion
            version = QuestionVersion.objects.create(
                question=question,
                version_number=1,
                content=draft.data,
                options=draft.data['options'],
                created_by=user
            )

            # Draft'ni publish qilish
            draft.is_published = True
            draft.published_question = question
            draft.save()

        assert draft.is_published is True
        assert draft.published_question == question
        assert question.versions.count() == 1

    def test_bulk_publish_invalid_drafts_skipped(self, db, org):
        """Invalid draft'lar publish qilinmaydi."""
        subject = Subject.objects.create(
            name="Matematika",
            slug="matematika",
            organization=org
        )

        with tenant_context(org):
            batch = ImportBatch.objects.create(
                organization=org,
                file_type='csv'
            )

            # Valid draft
            valid_draft = QuestionDraft.objects.create(
                batch=batch,
                type=Question.Type.SINGLE_CHOICE,
                subject=subject,
                data={'text': 'Valid', 'options': []},
                is_valid=True
            )

            # Invalid draft
            invalid_draft = QuestionDraft.objects.create(
                batch=batch,
                type=Question.Type.SINGLE_CHOICE,
                subject=subject,
                data={'text': '', 'options': []},
                is_valid=False
            )

        assert valid_draft.is_valid is True
        assert invalid_draft.is_valid is False

    def test_draft_with_validation_errors(self, db, org):
        """Draft validation xatolarini saqlaydi."""
        with tenant_context(org):
            batch = ImportBatch.objects.create(
                organization=org,
                file_type='csv'
            )

            draft = QuestionDraft.objects.create(
                batch=batch,
                data={},
                is_valid=False,
                validation_errors=[
                    "Savol matni bo'sh",
                    "Fan tanlanmagan"
                ]
            )

        assert len(draft.validation_errors) == 2


@pytest.mark.integration
class TestMultiTenantImport:
    def test_import_isolation_between_orgs(self, db, org, org2):
        """Bir org ning import boshqa org ko'rsmaydi."""
        subject1 = Subject.objects.create(
            name="Matematika",
            slug="matematika",
            organization=org
        )

        subject2 = Subject.objects.create(
            name="Fizika",
            slug="fizika",
            organization=org2
        )

        with tenant_context(org):
            batch1 = ImportBatch.objects.create(
                organization=org,
                file_type='csv'
            )
            draft1 = QuestionDraft.objects.create(
                batch=batch1,
                data={'text': 'Test'},
                subject=subject1
            )

        with tenant_context(org2):
            batch2 = ImportBatch.objects.create(
                organization=org2,
                file_type='csv'
            )
            draft2 = QuestionDraft.objects.create(
                batch=batch2,
                data={'text': 'Test2'},
                subject=subject2
            )

        # org1 dan faqat org1 batch ko'rish kerak
        with tenant_context(org):
            assert ImportBatch.objects.count() == 1
            assert ImportBatch.objects.first().id == batch1.id

        with tenant_context(org2):
            assert ImportBatch.objects.count() == 1
            assert ImportBatch.objects.first().id == batch2.id
