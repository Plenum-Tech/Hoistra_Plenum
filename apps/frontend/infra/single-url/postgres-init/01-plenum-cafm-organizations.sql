-- Minimal plenum_cafm schema for local connector CRUD (organizations list, etc.).
-- WO service uses public schema; connector Plenum routes expect plenum_cafm.organizations.

CREATE SCHEMA IF NOT EXISTS plenum_cafm;

CREATE TABLE IF NOT EXISTS plenum_cafm.organizations (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    industry VARCHAR(150),
    address TEXT,
    country VARCHAR(100),
    timezone VARCHAR(100),
    status VARCHAR(50) NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
