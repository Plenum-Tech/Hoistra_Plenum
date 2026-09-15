-- Custom reports: a pinned question from a session chat, re-asked on a cadence by the server.
--
-- The shell has had custom reports for a while, but they lived in one browser's localStorage
-- and were re-run by the page only while it was open — close the tab and the report went
-- stale. plenum_cafm.pinned_run pins a prompt for a company but knows nothing about who
-- pinned it, how often it should run, or what it answered.
--
-- A report is a person's collection of cards. A card is one pinned question: what to ask,
-- how often (an interval, a daily time, or chosen days at a time, in the owner's zone), and
-- where it came from. Each refresh is a run — the orchestrator's answer to that question
-- against the live data at that moment, asked AS the owner, so it sees exactly the buildings
-- they see. Cards are removed one at a time; removal is soft so a run history is never
-- orphaned and a mistaken click can be undone by support.
CREATE TABLE IF NOT EXISTS plenum_cafm.reports (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id uuid,
    owner_user_id   uuid NOT NULL,
    name            varchar(200) NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    deleted_at      timestamptz
);
CREATE INDEX IF NOT EXISTS ix_reports_owner ON plenum_cafm.reports (owner_user_id) WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS plenum_cafm.report_cards (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    report_id          uuid NOT NULL REFERENCES plenum_cafm.reports(id) ON DELETE CASCADE,
    owner_user_id      uuid NOT NULL,
    organization_id    uuid,
    name               varchar(200) NOT NULL,
    prompt             text NOT NULL,
    source_session_id  varchar(120),
    source_page        varchar(80),
    source_message_id  varchar(120),
    -- {"every_minutes": 60} | {"daily_at": "02:00"} | {"days": [1,3,5], "time": "14:00"}
    refresh            jsonb NOT NULL DEFAULT '{"every_minutes": 60}'::jsonb,
    timezone           varchar(64) NOT NULL DEFAULT 'UTC',
    position           integer NOT NULL DEFAULT 0,
    enabled            boolean NOT NULL DEFAULT true,
    -- pending | running | ready | error | paused
    status             varchar(20) NOT NULL DEFAULT 'pending',
    last_run_at        timestamptz,
    last_tried_at      timestamptz,
    next_run_at        timestamptz,
    last_error         text,
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now(),
    removed_at         timestamptz
);
CREATE INDEX IF NOT EXISTS ix_report_cards_owner  ON plenum_cafm.report_cards (owner_user_id) WHERE removed_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_report_cards_report ON plenum_cafm.report_cards (report_id) WHERE removed_at IS NULL;
-- The scheduler's question, answered by one index: which enabled, live card is due next.
CREATE INDEX IF NOT EXISTS ix_report_cards_due ON plenum_cafm.report_cards (next_run_at)
    WHERE enabled AND removed_at IS NULL;

CREATE TABLE IF NOT EXISTS plenum_cafm.report_card_runs (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    card_id      uuid NOT NULL REFERENCES plenum_cafm.report_cards(id) ON DELETE CASCADE,
    ran_at       timestamptz NOT NULL DEFAULT now(),
    duration_ms  integer,
    ok           boolean NOT NULL,
    -- schedule | manual | seed (the answer the card was pinned from, before its first refresh)
    trigger      varchar(20) NOT NULL DEFAULT 'schedule',
    answer       text,
    tool_calls   jsonb,
    rich         jsonb,
    error        text
);
CREATE INDEX IF NOT EXISTS ix_report_card_runs_card ON plenum_cafm.report_card_runs (card_id, ran_at DESC);
