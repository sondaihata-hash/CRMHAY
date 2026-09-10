-- Payment tables are also created by SQLAlchemy's init_db() for SQLite and Postgres.
-- This idempotent reference migration is for deployments using an external runner.
CREATE TABLE IF NOT EXISTS subscription (
  id INTEGER PRIMARY KEY,
  organization_id INTEGER NOT NULL,
  plan VARCHAR(40) NOT NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'pending',
  starts_at TIMESTAMP NULL,
  ends_at TIMESTAMP NULL,
  created_at TIMESTAMP NOT NULL
);
CREATE TABLE IF NOT EXISTS payment (
  id INTEGER PRIMARY KEY,
  order_code BIGINT NOT NULL UNIQUE,
  organization_id INTEGER NOT NULL,
  user_id INTEGER NOT NULL,
  subscription_id INTEGER NOT NULL,
  plan VARCHAR(40) NOT NULL,
  amount INTEGER NOT NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'pending',
  checkout_url TEXT NULL,
  provider_payload TEXT NULL,
  paid_at TIMESTAMP NULL,
  created_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS order_payment (
  id INTEGER PRIMARY KEY,
  order_id INTEGER NOT NULL UNIQUE,
  organization_id INTEGER NOT NULL,
  user_id INTEGER NOT NULL,
  order_code BIGINT NOT NULL UNIQUE,
  amount INTEGER NOT NULL,
  payment_method VARCHAR(20) NOT NULL DEFAULT 'payos',
  status VARCHAR(20) NOT NULL DEFAULT 'pending',
  checkout_url TEXT NULL,
  qr_code TEXT NULL,
  provider_payload TEXT NULL,
  paid_at TIMESTAMP NULL,
  created_at TIMESTAMP NOT NULL
);
