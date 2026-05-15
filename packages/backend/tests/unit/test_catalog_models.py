import pytest
from django.utils import timezone
from apps.catalog.models import (
    Subject, Topic, Tag, Question, QuestionVersion,
    QuestionBank, ImportBatch, QuestionDraft, AIProviderConfig
)
from apps.organizations.models import Organization
from apps.accounts.models import CustomUser
from core.tenant import tenant_context, get_current_org


@pytest.mark.unit
class TestSubjectModel:
    def test_create_platform_global_subject(self, db):
        subject = Subject.objects.create(
            name="Matematika",
            slug="matematika",
            description="Asosiy matematika kursi"
        )
        assert subject.organization is None
        assert subject.is_active is True
        assert subject.name == "Matematika"

    def test_create_tenant_specific_subject(self, db, org):
        subject = Subject.objects.create(
            name="Xususiy Matematika",
            slug="xususiy-matematika",
            organization=org
        )
        assert subject.organization == org
        assert subject.name == "Xususiy Matematika"

    def test_subject_has_icon_field(self, db):
        subject = Subject.objects.create(name="Fan", slug="fan")
        assert hasattr(subject, 'icon')

    def test_subject_str_representation(self, db):
        subject = Subject.objects.create(name="Fizika", slug="fizika")
        assert str(subject) == "Fizika"


@pytest.mark.unit
class TestTopicHierarchy:
    def test_create_topic_with_subject(self, db):
        subject = Subject.objects.create(name="Matematika", slug="matematika")
        topic = Topic.objects.create(
            subject=subject,
            name="Algebra",
            order=1
        )
        assert topic.subject == subject
        assert topic.parent is None
        assert topic.name == "Algebra"

    def test_create_nested_topics(self, db):
        subject = Subject.objects.create(name="Matematika", slug="matematika")
        parent = Topic.objects.create(subject=subject, name="Algebra", order=1)
        child = Topic.objects.create(subject=subject, name="Polinom", parent=parent, order=1)

        assert child.parent == parent
        assert parent.children.count() == 1
        assert parent.children.first() == child

    def test_topic_ordering(self, db):
        subject = Subject.objects.create(name="Matematika", slug="matematika")
        t1 = Topic.objects.create(subject=subject, name="Topic1", order=2)
        t2 = Topic.objects.create(subject=subject, name="Topic2", order=1)

        topics = list(Topic.objects.all())
        assert topics[0] == t2  # order=1 comes first

    def test_topic_str_representation(self, db):
        subject = Subject.objects.create(name="Matematika", slug="matematika")
        topic = Topic.objects.create(subject=subject, name="Algebra", order=1)
        assert "Matematika" in str(topic)
        assert "Algebra" in str(topic)


@pytest.mark.unit
class TestTagModel:
    def test_create_tag(self, db):
        tag = Tag.objects.create(name="Algebra", slug="algebra")
        assert tag.name == "Algebra"
        assert tag.slug == "algebra"

    def test_tag_unique_name_and_slug(self, db):
        Tag.objects.create(name="Algebra", slug="algebra")
        with pytest.raises(Exception):  # IntegrityError
            Tag.objects.create(name="Algebra", slug="algebra")

    def test_tag_uuid_primary_key(self, db):
        tag = Tag.objects.create(name="Test", slug="test")
        assert tag.id is not None
        assert str(tag.id) != "1"  # UUID, not integer


@pytest.mark.unit
class TestQuestionModel:
    def test_create_single_choice_question(self, db, org):
        subject = Subject.objects.create(name="Matematika", slug="matematika")

        with tenant_context(org):
            question = Question.objects.create(
                organization=org,
                subject=subject,
                type=Question.Type.SINGLE_CHOICE,
                initial_difficulty='medium'
            )

        assert question.type == Question.Type.SINGLE_CHOICE
        assert question.organization == org
        assert question.current_version == 1

    def test_question_has_all_types(self, db, org):
        subject = Subject.objects.create(name="Matematika", slug="matematika")
        types = [
            Question.Type.SINGLE_CHOICE,
            Question.Type.MULTIPLE_CHOICE,
            Question.Type.MATCHING,
            Question.Type.ORDERING,
            Question.Type.FILL_BLANKS,
            Question.Type.OPEN_ENDED,
            Question.Type.FILE_UPLOAD
        ]

        for qtype in types:
            with tenant_context(org):
                q = Question.objects.create(
                    organization=org,
                    subject=subject,
                    type=qtype
                )
            assert q.type == qtype

    def test_question_with_topic_and_tags(self, db, org):
        subject = Subject.objects.create(name="Matematika", slug="matematika")
        topic = Topic.objects.create(subject=subject, name="Algebra", order=1)
        tag1 = Tag.objects.create(name="Linear", slug="linear")
        tag2 = Tag.objects.create(name="Equations", slug="equations")

        with tenant_context(org):
            question = Question.objects.create(
                organization=org,
                subject=subject,
                topic=topic
            )
            question.tags.add(tag1, tag2)

        assert question.tags.count() == 2
        assert tag1 in question.tags.all()

    def test_question_multilingual_support(self, db, org):
        subject = Subject.objects.create(name="Matematika", slug="matematika")

        with tenant_context(org):
            q_uz = Question.objects.create(
                organization=org,
                subject=subject,
                language='uz'
            )
            q_en = Question.objects.create(
                organization=org,
                subject=subject,
                language='en',
                parent_question=q_uz
            )

            assert q_uz.language == 'uz'
            assert q_en.parent_question == q_uz
            assert q_uz.translations.count() == 1

    def test_question_irt_parameters(self, db, org):
        subject = Subject.objects.create(name="Matematika", slug="matematika")

        with tenant_context(org):
            q = Question.objects.create(
                organization=org,
                subject=subject,
                difficulty_index=1.5,
                discrimination_index=0.8
            )

        assert q.difficulty_index == 1.5
        assert q.discrimination_index == 0.8

    def test_question_shuffling_enabled(self, db, org):
        subject = Subject.objects.create(name="Matematika", slug="matematika")

        with tenant_context(org):
            q = Question.objects.create(
                organization=org,
                subject=subject,
                shuffling_enabled=True
            )

        assert q.shuffling_enabled is True

    def test_question_str_representation(self, db, org):
        subject = Subject.objects.create(name="Matematika", slug="matematika")

        with tenant_context(org):
            q = Question.objects.create(
                organization=org,
                subject=subject,
                type=Question.Type.SINGLE_CHOICE
            )

        assert "SC" in str(q)
        assert "v1" in str(q)


@pytest.mark.unit
class TestQuestionVersion:
    def test_create_question_version(self, db, org):
        subject = Subject.objects.create(name="Matematika", slug="matematika")

        with tenant_context(org):
            q = Question.objects.create(organization=org, subject=subject)
            v1 = QuestionVersion.objects.create(
                question=q,
                version_number=1,
                content={
                    "text": "2 + 2 = ?",
                    "latex": "2 \\plus 2 = ?",
                    "media": []
                },
                options=[
                    {"id": 1, "text": "4", "is_correct": True},
                    {"id": 2, "text": "5", "is_correct": False}
                ]
            )

        assert v1.version_number == 1
        assert v1.content["text"] == "2 + 2 = ?"
        assert len(v1.options) == 2

    def test_version_with_explanation(self, db, org):
        subject = Subject.objects.create(name="Matematika", slug="matematika")

        with tenant_context(org):
            q = Question.objects.create(organization=org, subject=subject)
            v = QuestionVersion.objects.create(
                question=q,
                version_number=1,
                content={"text": "Test"},
                options=[],
                explanation={"text": "Bu oson savol"}
            )

        assert v.explanation["text"] == "Bu oson savol"

    def test_version_unique_together(self, db, org):
        subject = Subject.objects.create(name="Matematika", slug="matematika")

        with tenant_context(org):
            q = Question.objects.create(organization=org, subject=subject)
            QuestionVersion.objects.create(
                question=q, version_number=1,
                content={}, options=[]
            )

            with pytest.raises(Exception):  # IntegrityError
                QuestionVersion.objects.create(
                    question=q, version_number=1,
                    content={}, options=[]
                )

    def test_question_versions_ordered_by_version_number(self, db, org):
        subject = Subject.objects.create(name="Matematika", slug="matematika")

        with tenant_context(org):
            q = Question.objects.create(organization=org, subject=subject)
            v2 = QuestionVersion.objects.create(question=q, version_number=2, content={}, options=[])
            v1 = QuestionVersion.objects.create(question=q, version_number=1, content={}, options=[])
            v3 = QuestionVersion.objects.create(question=q, version_number=3, content={}, options=[])

        versions = list(q.versions.all())
        assert versions[0].version_number == 3  # Newest first


@pytest.mark.unit
class TestQuestionBank:
    def test_create_public_question_bank(self, db, org):
        bank = QuestionBank.objects.create(
            organization=org,
            name="Platform DTM",
            slug="platform-dtm",
            is_public=True
        )
        assert bank.is_public is True
        assert bank.organization == org

    def test_create_private_tenant_bank(self, db, org):
        with tenant_context(org):
            bank = QuestionBank.objects.create(
                organization=org,
                name="Private Bank",
                slug="private-bank",
                is_public=False
            )

        assert bank.organization == org
        assert bank.is_public is False

    def test_question_bank_with_multiple_questions(self, db, org):
        subject = Subject.objects.create(name="Matematika", slug="matematika")

        with tenant_context(org):
            q1 = Question.objects.create(organization=org, subject=subject)
            q2 = Question.objects.create(organization=org, subject=subject)

            bank = QuestionBank.objects.create(
                organization=org,
                name="Bank",
                slug="bank"
            )
            bank.questions.add(q1, q2)

            assert bank.questions.count() == 2

    def test_question_bank_str_representation(self, db, org):
        with tenant_context(org):
            bank = QuestionBank.objects.create(
                organization=org,
                name="My Bank",
                slug="my-bank"
            )

        assert str(bank) == "My Bank"


@pytest.mark.unit
class TestImportBatch:
    def test_create_import_batch(self, db, org, user):
        with tenant_context(org):
            batch = ImportBatch.objects.create(
                organization=org,
                file_type="xlsx",
                created_by=user
            )

        assert batch.status == ImportBatch.Status.PENDING
        assert batch.total_questions == 0
        assert batch.processed_questions == 0

    def test_import_batch_status_transitions(self, db, org, user):
        with tenant_context(org):
            batch = ImportBatch.objects.create(
                organization=org,
                file_type="xlsx",
                created_by=user,
                status=ImportBatch.Status.PENDING
            )

            batch.status = ImportBatch.Status.PROCESSING
            batch.save()
            batch.refresh_from_db()
            assert batch.status == ImportBatch.Status.PROCESSING

            batch.status = ImportBatch.Status.COMPLETED
            batch.save()
            batch.refresh_from_db()
            assert batch.status == ImportBatch.Status.COMPLETED

    def test_import_batch_with_error_log(self, db, org, user):
        with tenant_context(org):
            batch = ImportBatch.objects.create(
                organization=org,
                file_type="xlsx",
                created_by=user,
                error_log=["Xato 1", "Xato 2"]
            )

        assert len(batch.error_log) == 2


@pytest.mark.unit
class TestQuestionDraft:
    def test_create_draft_from_import(self, db, org, user):
        with tenant_context(org):
            batch = ImportBatch.objects.create(
                organization=org,
                file_type="xlsx",
                created_by=user
            )

            draft = QuestionDraft.objects.create(
                batch=batch,
                data={"text": "Savol matni", "options": ["A", "B", "C"]},
                type=Question.Type.SINGLE_CHOICE
            )

        assert draft.is_valid is True
        assert draft.is_published is False
        assert draft.data["text"] == "Savol matni"

    def test_draft_with_validation_errors(self, db, org, user):
        with tenant_context(org):
            batch = ImportBatch.objects.create(
                organization=org,
                file_type="xlsx",
                created_by=user
            )

            draft = QuestionDraft.objects.create(
                batch=batch,
                data={},
                is_valid=False,
                validation_errors=["Savol matni bo'sh", "Javob yo'q"]
            )

        assert draft.is_valid is False
        assert len(draft.validation_errors) == 2

    def test_draft_to_published_conversion(self, db, org, user):
        subject = Subject.objects.create(name="Matematika", slug="matematika")

        with tenant_context(org):
            batch = ImportBatch.objects.create(
                organization=org,
                file_type="xlsx",
                created_by=user
            )

            draft = QuestionDraft.objects.create(
                batch=batch,
                data={"text": "Test", "options": []},
                type=Question.Type.SINGLE_CHOICE,
                subject=subject
            )

            question = Question.objects.create(
                organization=org,
                subject=subject,
                type=draft.type
            )

            draft.is_published = True
            draft.published_question = question
            draft.save()

        assert draft.is_published is True
        assert draft.published_question == question


@pytest.mark.unit
class TestAIProviderConfig:
    def test_create_ai_provider_config(self, db):
        config = AIProviderConfig.objects.create(
            name="Asosiy Claude",
            provider=AIProviderConfig.Provider.CLAUDE,
            model="claude-sonnet-4-6",
            api_key="sk-test",
            use_for=AIProviderConfig.UseFor.PARSER,
            is_active=True,
            priority=1
        )

        assert config.provider == AIProviderConfig.Provider.CLAUDE
        assert config.is_active is True
        assert config.priority == 1

    def test_multiple_providers_with_priority(self, db):
        config1 = AIProviderConfig.objects.create(
            name="Claude",
            provider=AIProviderConfig.Provider.CLAUDE,
            model="claude-sonnet-4-6",
            api_key="sk-1",
            priority=1
        )

        config2 = AIProviderConfig.objects.create(
            name="OpenAI Fallback",
            provider=AIProviderConfig.Provider.OPENAI,
            model="gpt-4o",
            api_key="sk-2",
            priority=2
        )

        configs = list(AIProviderConfig.objects.all())
        assert configs[0].priority < configs[1].priority

    def test_ollama_provider_without_api_key(self, db):
        config = AIProviderConfig.objects.create(
            name="Ollama Local",
            provider=AIProviderConfig.Provider.OLLAMA,
            model="llama3",
            base_url="http://localhost:11434",
            is_active=True
        )

        assert config.api_key == ""
        assert config.base_url == "http://localhost:11434"

    def test_provider_str_representation(self, db):
        config = AIProviderConfig.objects.create(
            name="Test",
            provider=AIProviderConfig.Provider.CLAUDE,
            model="claude-sonnet-4-6",
            api_key="test",
            priority=1,
            is_active=True
        )

        assert "✓" in str(config)
        assert "[1]" in str(config)
        assert "claude-sonnet-4-6" in str(config)

    def test_inactive_provider_representation(self, db):
        config = AIProviderConfig.objects.create(
            name="Inactive",
            provider=AIProviderConfig.Provider.OPENAI,
            model="gpt-4",
            api_key="test",
            is_active=False
        )

        assert "✗" in str(config)


@pytest.mark.unit
class TestTenantIsolationCatalog:
    def test_question_tenant_isolation(self, db, org, org2):
        subject = Subject.objects.create(name="Math", slug="math")

        with tenant_context(org):
            q1 = Question.objects.create(organization=org, subject=subject)

        with tenant_context(org2):
            q2 = Question.objects.create(organization=org2, subject=subject)

        with tenant_context(org):
            assert Question.objects.count() == 1
            assert q1 in Question.objects.all()
            assert q2 not in Question.objects.all()

    def test_global_objects_bypass_isolation(self, db, org, org2):
        subject = Subject.objects.create(name="Math", slug="math")

        with tenant_context(org):
            q1 = Question.objects.create(organization=org, subject=subject)

        with tenant_context(org2):
            q2 = Question.objects.create(organization=org2, subject=subject)

        all_questions = Question.global_objects.all()
        assert all_questions.count() == 2
        assert q1 in all_questions
        assert q2 in all_questions
