DROP SCHEMA IF EXISTS demo CASCADE;
CREATE SCHEMA demo;

-- Base tables
CREATE TABLE demo.customers (
    id          bigint PRIMARY KEY,
    name        text NOT NULL,
    amount      numeric(12,2),
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE demo.orders (
    id           bigint PRIMARY KEY,
    customer_id  bigint NOT NULL,
    amount       numeric(12,2),
    created_at   timestamptz NOT NULL DEFAULT now()
);

-- Root view
CREATE VIEW demo.v_customer AS
SELECT
    c.id,
    c.name,
    SUM(o.amount) AS amount,
    MAX(o.created_at) AS last_order_at
FROM demo.customers c
LEFT JOIN demo.orders o
    ON o.customer_id = c.id
GROUP BY c.id, c.name;

-- Dependent view #1
CREATE VIEW demo.v_customer_stats AS
SELECT
    id,
    name,
    amount,
    amount * 0.10 AS estimated_tax
FROM demo.v_customer;

-- Dependent view #2
CREATE VIEW demo.v_customer_export AS
SELECT
    id,
    name,
    amount
FROM demo.v_customer;

-- MV depending on v_customer_stats
CREATE MATERIALIZED VIEW demo.mv_customer_stats AS
SELECT
    id,
    name,
    amount,
    estimated_tax
FROM demo.v_customer_stats;

-- View depending on the MV
CREATE VIEW demo.v_customer_report AS
SELECT
    id,
    name,
    amount,
    estimated_tax
FROM demo.mv_customer_stats;

-- Add metadata we eventually want to preserve
CREATE INDEX idx_mv_customer_stats_id
    ON demo.mv_customer_stats(id);

COMMENT ON VIEW demo.v_customer IS
    'Root view for migration testing';

COMMENT ON MATERIALIZED VIEW demo.mv_customer_stats IS
    'Materialized customer statistics';

INSERT INTO demo.customers (id, name, amount)
VALUES
    (1, 'Alice', 100),
    (2, 'Bob', 200);

INSERT INTO demo.orders (id, customer_id, amount)
VALUES
    (1, 1, 50),
    (2, 1, 50),
    (3, 2, 200);