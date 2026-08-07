-- Local dev bootstrap. Production uses Alembic migrations.
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "citext";
CREATE EXTENSION IF NOT EXISTS "vector";
CREATE EXTENSION IF NOT EXISTS "pg_stat_statements";

-- Workers need cross-org read for cache lookups. An explicit narrow role
-- rather than BYPASSRLS on the application role.
DO $$ BEGIN
  CREATE ROLE reelsedits_worker;
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
