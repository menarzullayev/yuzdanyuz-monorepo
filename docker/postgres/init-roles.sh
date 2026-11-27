#!/bin/sh
# First-boot role setup for docker-compose.prod.yml (runs once, as the superuser, from
# /docker-entrypoint-initdb.d on an empty data directory). Same SQL as
# packages/backend/docs/deployment/database-roles.md:
#   yuzdanyuz_owner   owns the schema, used only by the migrate service
#   yuzdanyuz         runtime role (web, channels, Celery) — NOBYPASSRLS, DML only
#   yuzdanyuz_backup  read-only, BYPASSRLS, for pg_dump
#   yuzdanyuz_definer NOLOGIN owner of the SECURITY DEFINER search helpers
# Passwords come from the environment and are passed to psql as variables, never echoed.
set -eu

: "${DB_MIGRATE_PASSWORD:?}" "${DB_PASSWORD:?}" "${DB_BACKUP_PASSWORD:?}"
DB_NAME="${DB_NAME:-yuzdanyuz_db}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
     -v owner_pw="$DB_MIGRATE_PASSWORD" -v runtime_pw="$DB_PASSWORD" \
     -v backup_pw="$DB_BACKUP_PASSWORD" -v db_name="$DB_NAME" <<'SQL'
CREATE ROLE yuzdanyuz_owner  LOGIN PASSWORD :'owner_pw';
CREATE ROLE yuzdanyuz        LOGIN PASSWORD :'runtime_pw'
       NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
CREATE ROLE yuzdanyuz_backup LOGIN PASSWORD :'backup_pw' BYPASSRLS;
GRANT pg_read_all_data TO yuzdanyuz_backup;
CREATE ROLE yuzdanyuz_definer NOLOGIN BYPASSRLS;
GRANT yuzdanyuz_definer TO yuzdanyuz_owner;
ALTER ROLE yuzdanyuz_owner SET app.rls_bypass = 'all';
CREATE DATABASE :"db_name" OWNER yuzdanyuz_owner;
\c :"db_name"
ALTER SCHEMA public OWNER TO yuzdanyuz_owner;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
SQL
