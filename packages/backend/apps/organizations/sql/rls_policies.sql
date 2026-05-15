-- PostgreSQL Row Level Security policies
-- Bu script Task 10 (Production) da ishga tushiriladi.
-- Hozircha Django TenantManager Layer 1 sifatida ishlaydi.
--
-- Ishlatish:
--   psql $DATABASE_URL -f apps/organizations/sql/rls_policies.sql
--
-- Yoki management command:
--   python manage.py setup_rls

-- ============================================================
-- Yordamchi funksiya: joriy organization_id ni olish
-- Django app_user.set_config() orqali har so'rovda o'rnatiladi
-- ============================================================
CREATE OR REPLACE FUNCTION current_org_id() RETURNS bigint AS $$
    SELECT NULLIF(current_setting('app.current_org_id', TRUE), '')::bigint;
$$ LANGUAGE sql STABLE;

-- ============================================================
-- Jadvallar uchun RLS yoqish va policy qo'shish
-- Har bir tenant-ega jadval uchun quyidagi pattern takrorlanadi:
--
--   ALTER TABLE <schema>_<table> ENABLE ROW LEVEL SECURITY;
--   ALTER TABLE <schema>_<table> FORCE ROW LEVEL SECURITY;
--
--   CREATE POLICY tenant_isolation ON <schema>_<table>
--       USING (
--           organization_id = current_org_id()
--           OR current_org_id() IS NULL          -- superuser/worker bypass
--           OR current_setting('app.bypass_rls', TRUE) = 'on'
--       );
-- ============================================================

-- catalog app
ALTER TABLE catalog_question         ENABLE ROW LEVEL SECURITY;
ALTER TABLE catalog_question         FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON catalog_question
    USING (
        organization_id = current_org_id()
        OR current_org_id() IS NULL
        OR current_setting('app.bypass_rls', TRUE) = 'on'
    );

-- exams app
ALTER TABLE exams_mockexam           ENABLE ROW LEVEL SECURITY;
ALTER TABLE exams_mockexam           FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON exams_mockexam
    USING (
        organization_id = current_org_id()
        OR current_org_id() IS NULL
        OR current_setting('app.bypass_rls', TRUE) = 'on'
    );

ALTER TABLE exams_practicesession    ENABLE ROW LEVEL SECURITY;
ALTER TABLE exams_practicesession    FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON exams_practicesession
    USING (
        organization_id = current_org_id()
        OR current_org_id() IS NULL
        OR current_setting('app.bypass_rls', TRUE) = 'on'
    );

-- Qo'shimcha jadvallar Task 10 da shu faylga qo'shiladi.
-- Yangi TenantMixin ishlatgan har bir model shu ro'yxatga kiritilishi kerak.
