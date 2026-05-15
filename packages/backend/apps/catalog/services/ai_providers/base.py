from abc import ABC, abstractmethod

SYSTEM_PROMPT = (
    "Sen professional EdTech parser assistentisan. "
    "Faqat valid JSON qaytar — hech qanday markdown, izoh yoki prefiks qo'shma."
)

USER_PROMPT = """Quyidagi matndan test savolini ajratib ol va aniq JSON formatida qaytar.

JSON strukturasi:
{{
    "text": "Savol matni",
    "options": [
        {{"label": "A", "text": "Variant 1", "is_correct": true}},
        {{"label": "B", "text": "Variant 2", "is_correct": false}},
        {{"label": "C", "text": "Variant 3", "is_correct": false}},
        {{"label": "D", "text": "Variant 4", "is_correct": false}}
    ],
    "explanation": "To'g'ri javob sababini qisqacha izohla"
}}

Matn:
{text}"""


class BaseAIProvider(ABC):
    """Barcha AI provayder klasslari shu interfeysi amalga oshiradi."""

    @abstractmethod
    def parse_text_block(self, text: str) -> dict:
        """Matn blokini savol+variantlar JSON ga aylantiradi."""

    def _prompt(self, text: str) -> str:
        return USER_PROMPT.format(text=text)
