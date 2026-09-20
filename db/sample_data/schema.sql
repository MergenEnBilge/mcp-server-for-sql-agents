-- Sample "customer" database: a small online shop.
--
-- This one file is loaded into BOTH Postgres (org_data) and SQLite so the two
-- engines hold the identical schema. That's what lets us prove the MCP server
-- doesn't care which engine it's talking to. To keep it portable:
--   * primary keys are plain INTEGER and every row gets an explicit id
--     (nothing here ever inserts without one), so there are no sequences or
--     AUTOINCREMENT to reconcile between engines
--   * only types both engines accept: INTEGER, VARCHAR, TEXT, NUMERIC, BOOLEAN, TIMESTAMP
--   * no engine-specific features (no ENUMs, arrays, JSONB, generated columns)

CREATE TABLE customers (
    id          INTEGER      PRIMARY KEY,
    email       VARCHAR(255) NOT NULL UNIQUE,
    full_name   VARCHAR(120) NOT NULL,
    country     VARCHAR(2)   NOT NULL,          -- ISO 3166-1 alpha-2
    created_at  TIMESTAMP    NOT NULL,
    is_active   BOOLEAN      NOT NULL DEFAULT TRUE
);

CREATE TABLE categories (
    id          INTEGER      PRIMARY KEY,
    name        VARCHAR(80)  NOT NULL UNIQUE,
    parent_id   INTEGER      REFERENCES categories (id)   -- NULL for top-level categories
);

CREATE TABLE products (
    id           INTEGER       PRIMARY KEY,
    sku          VARCHAR(32)   NOT NULL UNIQUE,
    name         VARCHAR(160)  NOT NULL,
    category_id  INTEGER       NOT NULL REFERENCES categories (id),
    unit_price   NUMERIC(10,2) NOT NULL CHECK (unit_price >= 0),
    is_active    BOOLEAN       NOT NULL DEFAULT TRUE
);

CREATE TABLE orders (
    id                INTEGER     PRIMARY KEY,
    customer_id       INTEGER     NOT NULL REFERENCES customers (id),
    status            VARCHAR(20) NOT NULL
        CHECK (status IN ('pending', 'paid', 'shipped', 'delivered', 'cancelled', 'refunded')),
    ordered_at        TIMESTAMP   NOT NULL,
    shipping_country  VARCHAR(2)  NOT NULL
);

CREATE TABLE order_items (
    id          INTEGER       PRIMARY KEY,
    order_id    INTEGER       NOT NULL REFERENCES orders (id),
    product_id  INTEGER       NOT NULL REFERENCES products (id),
    quantity    INTEGER       NOT NULL CHECK (quantity > 0),
    unit_price  NUMERIC(10,2) NOT NULL CHECK (unit_price >= 0),  -- price at time of purchase
    UNIQUE (order_id, product_id)
);

CREATE TABLE payments (
    id        INTEGER       PRIMARY KEY,
    order_id  INTEGER       NOT NULL REFERENCES orders (id),
    method    VARCHAR(20)   NOT NULL CHECK (method IN ('card', 'paypal', 'bank_transfer')),
    amount    NUMERIC(10,2) NOT NULL CHECK (amount >= 0),
    status    VARCHAR(20)   NOT NULL CHECK (status IN ('captured', 'refunded', 'failed')),
    paid_at   TIMESTAMP     NOT NULL
);

CREATE TABLE reviews (
    id           INTEGER     PRIMARY KEY,
    product_id   INTEGER     NOT NULL REFERENCES products (id),
    customer_id  INTEGER     NOT NULL REFERENCES customers (id),
    rating       INTEGER     NOT NULL CHECK (rating BETWEEN 1 AND 5),
    body         TEXT        NOT NULL,   -- free text written by customers: untrusted content
    created_at   TIMESTAMP   NOT NULL
);

CREATE INDEX idx_products_category    ON products (category_id);
CREATE INDEX idx_orders_customer      ON orders (customer_id);
CREATE INDEX idx_orders_ordered_at    ON orders (ordered_at);
CREATE INDEX idx_order_items_product  ON order_items (product_id);
CREATE INDEX idx_payments_order       ON payments (order_id);
CREATE INDEX idx_reviews_product      ON reviews (product_id);
