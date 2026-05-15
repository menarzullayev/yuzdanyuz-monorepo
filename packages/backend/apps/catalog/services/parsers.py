import csv
import logging
import re

import docx
import openpyxl
import pypdf
from django.conf import settings

from apps.catalog.models import ImportBatch, QuestionDraft

from .ai_parser import AIParserService

logger = logging.getLogger(__name__)

_LABELS = ['A', 'B', 'C', 'D', 'E']
_CORRECT_LETTERS = set(_LABELS)

# Ikkala parser uchun umumiy regex
_Q_PATTERN = re.compile(r'^(\d+)[.\)]\s*(.*)')
_OPT_PATTERN = re.compile(r'^\*?\s*([A-E])[.\)]\s*(.*)')


def _validate(q_data: dict) -> bool:
    """Savol to'g'riligini tekshirish: matn + ≥2 variant + kamida 1 to'g'ri javob."""
    has_text = bool(q_data.get('text', '').strip())
    options = q_data.get('options', [])
    has_options = len(options) >= 2
    has_correct = any(o.get('is_correct') for o in options)
    return has_text and has_options and has_correct


def _ai_enabled() -> bool:
    """ENABLE_AI_PARSER feature flag — settings yoki env orqali boshqariladi."""
    return getattr(settings, 'ENABLE_AI_PARSER', True)


class DocxParserService:
    """
    Microsoft Word (.docx) fayllarini tahlil qiluvchi servis.

    To'g'ri javob deteksiyasi:
      1. Variant boshi `*` bilan boshlanadi  → *A) to'g'ri javob
      2. Butun paragraf bold formatida

    ENABLE_AI_PARSER=True bo'lsa tuzilmagan bloklar AI ga yuboriladi.
    ENABLE_AI_PARSER=False bo'lsa faqat regex ishlaydi (bepul).
    """

    def __init__(self, import_batch: ImportBatch):
        self.batch = import_batch
        self.ai_parser = AIParserService()

    def parse(self) -> int:
        self.batch.status = ImportBatch.Status.PROCESSING
        self.batch.save()

        try:
            questions = self._extract_questions()
            for q_data in questions:
                QuestionDraft.objects.create(
                    batch=self.batch,
                    data=q_data,
                    is_valid=_validate(q_data),
                )
            self.batch.status = ImportBatch.Status.COMPLETED
            self.batch.total_questions = len(questions)
        except Exception as exc:
            self.batch.status = ImportBatch.Status.FAILED
            self.batch.error_log = {'error': str(exc)}
        finally:
            self.batch.save()

        return self.batch.total_questions

    def _extract_questions(self) -> list:
        document = docx.Document(self.batch.file.path)  # lazily opened
        questions = []
        current = None

        for para in document.paragraphs:
            text = para.text.strip()
            images = self._extract_images(para)

            if not text and not images:
                continue

            q_match = _Q_PATTERN.match(text) if text else None
            opt_match = _OPT_PATTERN.match(text) if text else None

            if q_match:
                if current:
                    questions.append(current)
                current = {'text': q_match.group(2), 'options': [], 'media': images}

            elif opt_match and current:
                is_correct = text.startswith('*') or self._is_bold(para)
                current['options'].append(
                    {
                        'label': opt_match.group(1),
                        'text': opt_match.group(2),
                        'is_correct': is_correct,
                    }
                )
                if images:
                    current['options'][-1]['media'] = images

            elif current:
                if text:
                    current['text'] += '\n' + text
                if images:
                    current['media'].extend(images)

            elif text:
                # Tuzilmagan blok — ENABLE_AI_PARSER=True bo'lsa AI ga yuboriladi
                if _ai_enabled():
                    parsed = self.ai_parser.parse_text_block(text)
                    if 'error' not in parsed:
                        questions.append(parsed)
                else:
                    logger.debug("AI parser o'chirilgan, blok o'tkazildi: %.60s", text)

        if current:
            questions.append(current)

        return questions

    @staticmethod
    def _is_bold(para) -> bool:
        runs = [r for r in para.runs if r.text.strip()]
        return bool(runs) and all(r.bold for r in runs)

    @staticmethod
    def _extract_images(para) -> list:
        return [
            {'type': 'image', 'status': 'extracted_from_docx'}
            for run in para.runs
            if 'drawing' in run._element.xml
        ]


class PdfParserService:
    """
    PDF fayllarini tahlil qiluvchi servis.

    Ishlash tartibi:
      1. pypdf orqali har bir sahifadan matn ajratiladi
      2. Satrlar bo'yicha DocxParser bilan bir xil regex qo'llanadi
      3. ENABLE_AI_PARSER=True → tuzilmagan bloklar AI ga yuboriladi
      4. ENABLE_AI_PARSER=False → faqat regex (bepul)

    Cheklov: Scan qilingan (rasm) PDFlar uchun OCR kerak — hozircha qo'llab-quvvatlanmaydi.
    """

    def __init__(self, import_batch: ImportBatch):
        self.batch = import_batch
        self.ai_parser = AIParserService()

    def parse(self) -> int:
        self.batch.status = ImportBatch.Status.PROCESSING
        self.batch.save()

        try:
            questions = self._extract_questions()
            for q_data in questions:
                QuestionDraft.objects.create(
                    batch=self.batch,
                    data=q_data,
                    is_valid=_validate(q_data),
                )
            self.batch.status = ImportBatch.Status.COMPLETED
            self.batch.total_questions = len(questions)
        except Exception as exc:
            self.batch.status = ImportBatch.Status.FAILED
            self.batch.error_log = {'error': str(exc)}
        finally:
            self.batch.save()

        return self.batch.total_questions

    def _extract_questions(self) -> list:
        reader = pypdf.PdfReader(self.batch.file.path)
        questions = []
        current = None

        # Barcha sahifalar ketma-ket bitta oqim sifatida qayta ishlanadi
        for page in reader.pages:
            page_text = page.extract_text() or ''
            for line in page_text.splitlines():
                line = line.strip()
                if not line:
                    continue

                q_match = _Q_PATTERN.match(line)
                opt_match = _OPT_PATTERN.match(line)

                if q_match:
                    if current:
                        questions.append(current)
                    current = {'text': q_match.group(2), 'options': []}

                elif opt_match and current:
                    is_correct = line.startswith('*')
                    current['options'].append(
                        {
                            'label': opt_match.group(1),
                            'text': opt_match.group(2),
                            'is_correct': is_correct,
                        }
                    )

                elif current:
                    current['text'] += '\n' + line

                elif _ai_enabled():
                    parsed = self.ai_parser.parse_text_block(line)
                    if 'error' not in parsed:
                        questions.append(parsed)
                else:
                    logger.debug("AI parser o'chirilgan, blok o'tkazildi: %.60s", line)

        if current:
            questions.append(current)

        return questions


class ExcelParserService:
    """
    Excel (.xlsx) fayllarini qat'iy shablon bo'yicha tahlil qiluvchi servis.

    Shablon ustunlari:
      0: savol matni | 1: A varianti | 2: B varianti |
      3: C varianti  | 4: D varianti | 5: to'g'ri javob harfi (A/B/C/D)
    """

    def __init__(self, import_batch: ImportBatch):
        self.batch = import_batch

    def parse(self) -> int:
        self.batch.status = ImportBatch.Status.PROCESSING
        self.batch.save()

        try:
            count = self._process_sheet()
            self.batch.status = ImportBatch.Status.COMPLETED
            self.batch.total_questions = count
        except Exception as exc:
            self.batch.status = ImportBatch.Status.FAILED
            self.batch.error_log = {'error': str(exc)}
        finally:
            self.batch.save()

        return self.batch.total_questions

    def _process_sheet(self) -> int:
        workbook = openpyxl.load_workbook(self.batch.file.path, data_only=True)  # lazily opened
        sheet = workbook.active
        if sheet is None:
            raise ValueError('Workbook da faol varaq (active sheet) topilmadi')

        count = 0
        for row in sheet.iter_rows(min_row=2, values_only=True):
            if not row or not row[0]:
                continue

            text = self._cell(row, 0)
            correct = self._cell(row, 5).upper()

            options = [
                {
                    'label': lbl,
                    'text': self._cell(row, i + 1),
                    'is_correct': correct == lbl,
                }
                for i, lbl in enumerate('ABCD')
            ]

            q_data = {'text': text, 'options': options}
            QuestionDraft.objects.create(
                batch=self.batch,
                data=q_data,
                is_valid=_validate(q_data),
            )
            count += 1

        return count

    @staticmethod
    def _cell(row: tuple, idx: int, default: str = '') -> str:
        """None va IndexError dan himoyalangan hujayra o'qish."""
        if idx < len(row) and row[idx] is not None:
            return str(row[idx]).strip()
        return default


class CSVParserService:
    """
    CSV fayllarini tahlil qiluvchi servis.

    Ustun tartibi (Excel shablon bilan bir xil):
      0: savol matni | 1: A varianti | 2: B varianti |
      3: C varianti  | 4: D varianti | 5: to'g'ri javob harfi (A/B/C/D)

    Agar oxirgi ustun yagona A–E harfi bo'lsa — to'g'ri javob harfi deb qabul qilinadi.
    """

    def __init__(self, import_batch: ImportBatch):
        self.batch = import_batch

    def parse(self) -> int:
        self.batch.status = ImportBatch.Status.PROCESSING
        self.batch.save()

        try:
            count = self._process_file()
            self.batch.status = ImportBatch.Status.COMPLETED
            self.batch.total_questions = count
        except Exception as exc:
            self.batch.status = ImportBatch.Status.FAILED
            self.batch.error_log = {'error': str(exc)}
        finally:
            self.batch.save()

        return self.batch.total_questions

    def _process_file(self) -> int:
        count = 0
        with open(self.batch.file.path, encoding='utf-8-sig') as f:
            reader = csv.reader(f)
            next(reader, None)  # sarlavha qatorini o'tkazib yuborish
            for row in reader:
                q_data = self._parse_row(row)
                if q_data is None:
                    continue
                QuestionDraft.objects.create(
                    batch=self.batch,
                    data=q_data,
                    is_valid=_validate(q_data),
                )
                count += 1
        return count

    @staticmethod
    def _parse_row(row: list) -> dict | None:
        if len(row) < 3:
            return None
        text = row[0].strip()
        if not text:
            return None

        last = row[-1].strip().upper()
        if last in _CORRECT_LETTERS:
            option_cols = row[1:-1]
            correct = last
        else:
            option_cols = row[1:]
            correct = ''

        options = [
            {
                'label': _LABELS[i],
                'text': val.strip(),
                'is_correct': _LABELS[i] == correct,
            }
            for i, val in enumerate(option_cols)
            if i < len(_LABELS)
        ]

        return {'text': text, 'options': options}


class ParserDispatcher:
    """
    ImportBatch.file_type ga qarab to'g'ri parser klassini qaytaradi.

    Qo'llab-quvvatlanadigan formatlar: docx, pdf, xlsx, csv
    """

    _PARSERS = {
        'docx': DocxParserService,
        'pdf': PdfParserService,
        'xlsx': ExcelParserService,
        'csv': CSVParserService,
    }

    @classmethod
    def get_parser(cls, batch: ImportBatch):
        klass = cls._PARSERS.get(batch.file_type.lower())
        if not klass:
            raise ValueError(
                f"Qo'llab-quvvatlanmaydigan fayl turi: '{batch.file_type}'. "
                f'Ruxsat etilganlar: {", ".join(cls._PARSERS)}'
            )
        return klass(batch)

    @classmethod
    def parse(cls, batch: ImportBatch) -> int:
        """Bir qator bilan to'liq parse jarayonini boshlash."""
        return cls.get_parser(batch).parse()
