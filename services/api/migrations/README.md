# Migrations

Alembic migrations for the Postgres schema described in
[docs/11-database-schema.md](../../../docs/11-database-schema.md).

```bash
alembic revision --autogenerate -m "description"
alembic upgrade head
```

## Rules

**Every migration is reversible or explicitly marked irreversible.** An
irreversible migration in a video platform means a bad deploy cannot be rolled
back while jobs are in flight.

**Index creation uses `CONCURRENTLY` in production.** `segments` and `jobs` grow
fast; a plain `CREATE INDEX` takes an `ACCESS EXCLUSIVE` lock and stalls every
render.

**Blueprints are never dropped or truncated by a migration.** They are the
corpus (docs/02 section 4) and they are kilobytes. A migration that would lose
them must be rewritten as a forward-migration of the `doc` JSONB instead.

**Partition maintenance is a scheduled job, not a migration.** `jobs` and
`renders` partition monthly; the creation of next month's partition runs from
cron, so a missed deploy does not cause an insert failure at midnight on the
first.
