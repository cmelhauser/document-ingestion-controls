-- =====================================================================
-- Canonical schema -- business document ingestion program
--
-- PostgreSQL dialect. Adjust types for other engines; the constraints are
-- the substance, not the syntax.
--
-- Two things this schema is built to guarantee:
--
--   1. Every financial row traces to a page range in a specific source PDF.
--      That is what makes an accuracy statement checkable rather than asserted.
--
--   2. Nothing is ever overwritten. Handwritten corrections are amendments
--      with a later effective time, stored beside their printed originals.
--      If a reading turns out wrong it is a correction, not an unrecoverable
--      loss.
--
-- Load order is enforced by the foreign keys below and restated in
-- references/data-model.md. Loading out of sequence produces orphans.
-- =====================================================================

BEGIN;

-- ---------------------------------------------------------------------
-- 1. Reference data  (load first -- everything depends on it)
-- ---------------------------------------------------------------------

CREATE TABLE currency (
    currency_code   CHAR(3) PRIMARY KEY,          -- ISO 4217
    description     TEXT NOT NULL
);

CREATE TABLE unit_of_measure (
    uom_code        VARCHAR(16) PRIMARY KEY,
    description     TEXT NOT NULL,
    dimension       VARCHAR(16)                   -- mass | volume | count | length
);

CREATE TABLE country (
    country_code    CHAR(2) PRIMARY KEY,          -- ISO 3166-1 alpha-2
    name            TEXT NOT NULL
);

CREATE TABLE charge_code (
    charge_code     VARCHAR(32) PRIMARY KEY,
    description     TEXT NOT NULL,
    category        VARCHAR(32)                   -- linehaul | fuel | accessorial | tax | duty
);

CREATE TABLE document_type (
    document_type   VARCHAR(48) PRIMARY KEY,
    description     TEXT NOT NULL,
    is_financial    BOOLEAN NOT NULL DEFAULT TRUE
);

-- ---------------------------------------------------------------------
-- Shared enums
-- ---------------------------------------------------------------------

CREATE TYPE review_status_t AS ENUM (
    'auto_accepted',        -- passed consensus and arithmetic self-proof
    'sampled_verified',     -- drawn into the QA sample and checked by hand
    'exception_resolved',   -- failed a gate, worked, resolved
    'open_exception'        -- failed a gate, not yet resolved
);

CREATE TYPE amendment_source_t AS ENUM (
    'printed', 'handwritten', 'manual', 'system'
);

CREATE TYPE consensus_flag_t AS ENUM (
    'consensus_2of2', 'consensus_2of3', 'consensus_3of3',
    'no_consensus', 'single_engine'
);

CREATE TYPE branch_t AS ENUM ('A', 'B', 'B-rescan');

CREATE TYPE party_role_t AS ENUM (
    'shipper', 'consignee', 'biller', 'payer', 'carrier', 'notify'
);

CREATE TYPE match_method_t AS ENUM (
    'exact_reference', 'amount_date_party', 'fuzzy', 'manual', 'unmatched'
);

CREATE TYPE event_type_t AS ENUM (
    'purchase', 'payment', 'credit', 'adjustment', 'accrual'
);

-- A sale's originating office is distinct from ship-from, customer, and
-- destination geography. Keep the approved, effective-dated master explicit.
CREATE TABLE selling_location (
    selling_location_key UUID PRIMARY KEY,
    natural_key         TEXT NOT NULL,
    location_name       TEXT NOT NULL,
    city                TEXT,
    state_province      TEXT,
    postal_code         VARCHAR(24),
    country_code        CHAR(2) REFERENCES country(country_code),
    territory_name      TEXT,
    effective_from      DATE,
    effective_to        DATE,
    source_reference    TEXT,
    review_status       review_status_t NOT NULL,
    batch_id            UUID NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_selling_location_natural UNIQUE (natural_key),
    CONSTRAINT ck_selling_location_dates CHECK (
        effective_to IS NULL OR effective_from IS NULL OR effective_to >= effective_from
    )
);

-- ---------------------------------------------------------------------
-- 2. Document registry
--
-- Loaded immediately after reference data because transactional rows reference it
-- for immutable provenance.
-- ---------------------------------------------------------------------

CREATE TABLE document (
    document_id         UUID PRIMARY KEY,
    natural_key         TEXT,                     -- vendor doc number where one exists
    document_type       VARCHAR(48) NOT NULL REFERENCES document_type(document_type),
    source_file         TEXT NOT NULL,
    source_page_range   VARCHAR(32) NOT NULL,
    source_sha256      CHAR(64) NOT NULL,         -- immutable source/page content fingerprint
    page_count          INTEGER,
    -- Provenance. Optional provenance is provenance that goes missing.
    engine              VARCHAR(64),
    engine_version      VARCHAR(64),
    run_timestamp       TIMESTAMPTZ NOT NULL,
    branch              branch_t NOT NULL,
    scan_dpi            INTEGER,
    jbig2_suspect       BOOLEAN NOT NULL DEFAULT FALSE,
    has_handwriting     BOOLEAN NOT NULL DEFAULT FALSE,
    consensus_flag      consensus_flag_t,
    arithmetic_status   VARCHAR(16),              -- proved | failed | not_provable
    source_confidence   NUMERIC(5,4),
    review_status       review_status_t NOT NULL,
    duplicate_of        UUID REFERENCES document(document_id),
    batch_id            UUID NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_document_natural ON document(natural_key);
CREATE INDEX idx_document_review  ON document(review_status);
CREATE INDEX idx_document_batch   ON document(batch_id);
CREATE INDEX idx_document_hw      ON document(has_handwriting) WHERE has_handwriting;

-- ---------------------------------------------------------------------
-- 3. Party  (load step 2)
--
-- One master for customers, vendors, and carriers. Role is expressed through
-- party_role, not through separate tables: a company that is both customer and
-- supplier -- common -- is one party with two roles. Modelling it as two
-- records breaks concentration analysis.
-- ---------------------------------------------------------------------

CREATE TABLE party (
    party_key           UUID PRIMARY KEY,
    natural_key         TEXT NOT NULL,            -- canonical normalized name
    canonical_name      TEXT NOT NULL,
    normalized_name     TEXT NOT NULL,
    tax_id              VARCHAR(48),
    source_document_id  UUID REFERENCES document(document_id),
    source_confidence   NUMERIC(5,4),
    review_status       review_status_t NOT NULL,
    merged_into         UUID REFERENCES party(party_key),   -- soft merge, reversible
    batch_id            UUID NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_party_natural UNIQUE (natural_key)
);

CREATE INDEX idx_party_normalized ON party(normalized_name);

-- Every name variant seen in the corpus. Retained so a merge can be audited
-- and undone -- the merge log is not a debugging aid, it is a deliverable.
CREATE TABLE party_name_variant (
    variant_id          BIGSERIAL PRIMARY KEY,
    party_key           UUID NOT NULL REFERENCES party(party_key),
    raw_name            TEXT NOT NULL,
    occurrence_count    INTEGER NOT NULL DEFAULT 1,
    similarity_score    NUMERIC(5,4),
    merge_reason        TEXT,
    merged_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    merged_by           TEXT,
    CONSTRAINT uq_variant UNIQUE (party_key, raw_name)
);

CREATE TABLE address (
    address_key         UUID PRIMARY KEY,
    party_key           UUID NOT NULL REFERENCES party(party_key),
    raw_address         TEXT NOT NULL,
    line1               TEXT,
    line2               TEXT,
    city                TEXT,
    state_province      TEXT,
    postal_code         VARCHAR(24),
    country_code        CHAR(2) REFERENCES country(country_code),
    address_type        VARCHAR(24),              -- billing | shipping | remit_to
    source_document_id  UUID REFERENCES document(document_id),
    review_status       review_status_t NOT NULL,
    batch_id            UUID NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE contact (
    contact_key         UUID PRIMARY KEY,
    party_key           UUID NOT NULL REFERENCES party(party_key),
    name                TEXT,
    title               TEXT,
    email               TEXT,
    phone               VARCHAR(48),
    source_document_id  UUID REFERENCES document(document_id),
    review_status       review_status_t NOT NULL,
    batch_id            UUID NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE party_role (
    party_role_id       BIGSERIAL PRIMARY KEY,
    party_key           UUID NOT NULL REFERENCES party(party_key),
    role                party_role_t NOT NULL,
    first_seen          DATE,
    last_seen           DATE,
    document_count      INTEGER NOT NULL DEFAULT 0,
    CONSTRAINT uq_party_role UNIQUE (party_key, role)
);

-- ---------------------------------------------------------------------
-- 4. Carrier, item, lane  (load step 3)
-- ---------------------------------------------------------------------

CREATE TABLE carrier (
    carrier_key         UUID PRIMARY KEY,
    party_key           UUID REFERENCES party(party_key),
    natural_key         TEXT NOT NULL,
    carrier_name        TEXT NOT NULL,
    scac                VARCHAR(8),
    mode                VARCHAR(24),              -- ltl | tl | parcel | ocean | air | rail
    review_status       review_status_t NOT NULL,
    batch_id            UUID NOT NULL,
    CONSTRAINT uq_carrier_natural UNIQUE (natural_key)
);

CREATE TABLE item (
    item_key            UUID PRIMARY KEY,
    natural_key         TEXT NOT NULL,
    item_code           TEXT,
    description         TEXT,
    hs_code             VARCHAR(24),
    default_uom         VARCHAR(16) REFERENCES unit_of_measure(uom_code),
    country_of_origin   CHAR(2) REFERENCES country(country_code),
    review_status       review_status_t NOT NULL,
    batch_id            UUID NOT NULL,
    CONSTRAINT uq_item_natural UNIQUE (natural_key)
);

-- Raw origin/destination strings are retained alongside the resolved lane.
-- They are needed whenever the resolution turns out wrong, which it will.
CREATE TABLE lane (
    lane_key            UUID PRIMARY KEY,
    natural_key         TEXT NOT NULL,
    origin_raw          TEXT,
    origin_city         TEXT,
    origin_state        TEXT,
    origin_postal       VARCHAR(24),
    origin_country      CHAR(2) REFERENCES country(country_code),
    destination_raw     TEXT,
    destination_city    TEXT,
    destination_state   TEXT,
    destination_postal  VARCHAR(24),
    destination_country CHAR(2) REFERENCES country(country_code),
    is_directional      BOOLEAN NOT NULL DEFAULT TRUE,
    review_status       review_status_t NOT NULL,
    batch_id            UUID NOT NULL,
    CONSTRAINT uq_lane_natural UNIQUE (natural_key)
);

-- ACK and job/project masters load before transactional rows so every
-- attributed dollar can carry a checked key instead of a free-text label.
CREATE TABLE acknowledgement (
    ack_key              UUID PRIMARY KEY,
    ack_number           TEXT NOT NULL,
    ack_date             DATE,
    customer_party_key   UUID REFERENCES party(party_key),
    selling_location_key UUID REFERENCES selling_location(selling_location_key),
    expected_value       NUMERIC(16,2),
    currency_code        CHAR(3) REFERENCES currency(currency_code),
    status               VARCHAR(32),
    source_document_id   UUID REFERENCES document(document_id),
    review_status        review_status_t NOT NULL,
    batch_id             UUID NOT NULL,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_ack_number UNIQUE (ack_number)
);

CREATE TABLE job (
    job_key              UUID PRIMARY KEY,
    job_number           TEXT NOT NULL,
    ack_key              UUID REFERENCES acknowledgement(ack_key),
    customer_party_key   UUID REFERENCES party(party_key),
    selling_location_key UUID REFERENCES selling_location(selling_location_key),
    job_date             DATE,
    expected_value       NUMERIC(16,2),
    currency_code        CHAR(3) REFERENCES currency(currency_code),
    status               VARCHAR(32),
    source_document_id   UUID REFERENCES document(document_id),
    review_status        review_status_t NOT NULL,
    batch_id             UUID NOT NULL,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_job_number UNIQUE (job_number)
);

-- ---------------------------------------------------------------------
-- Shipment
-- ---------------------------------------------------------------------

CREATE TABLE shipment (
    shipment_key        UUID PRIMARY KEY,
    natural_key         TEXT NOT NULL,            -- BOL / PRO / AWB
    bol_number          TEXT,
    pro_number          TEXT,
    awb_number          TEXT,
    container_number    TEXT,
    shipper_party_key   UUID REFERENCES party(party_key),
    consignee_party_key UUID REFERENCES party(party_key),
    carrier_key         UUID REFERENCES carrier(carrier_key),
    lane_key            UUID REFERENCES lane(lane_key),
    ship_date           DATE,
    delivery_date       DATE,
    committed_date      DATE,
    service_level       VARCHAR(48),
    equipment_type      VARCHAR(48),
    piece_count         INTEGER,
    gross_weight        NUMERIC(14,3),
    weight_uom          VARCHAR(16) REFERENCES unit_of_measure(uom_code),
    volume              NUMERIC(14,3),
    freight_terms       VARCHAR(24),              -- prepaid | collect | third_party
    -- POD fields. On most corpora these are handwritten, which is why the flag
    -- below matters more here than anywhere else in the schema.
    pod_signed          BOOLEAN,
    pod_received_by     TEXT,
    pod_pieces_received INTEGER,
    pod_from_handwriting BOOLEAN NOT NULL DEFAULT FALSE,
    ack_number          TEXT REFERENCES acknowledgement(ack_number),
    job_number          TEXT REFERENCES job(job_number),
    attribution_method  VARCHAR(48),
    selling_location_key UUID REFERENCES selling_location(selling_location_key),
    source_document_id  UUID REFERENCES document(document_id),
    source_confidence   NUMERIC(5,4),
    review_status       review_status_t NOT NULL,
    has_handwriting     BOOLEAN NOT NULL DEFAULT FALSE,
    batch_id            UUID NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_shipment_natural UNIQUE (natural_key),
    CONSTRAINT ck_shipment_dates CHECK (delivery_date IS NULL OR ship_date IS NULL
                                        OR delivery_date >= ship_date)
);

CREATE INDEX idx_shipment_lane    ON shipment(lane_key);
CREATE INDEX idx_shipment_carrier ON shipment(carrier_key);
CREATE INDEX idx_shipment_dates   ON shipment(ship_date, delivery_date);

-- ---------------------------------------------------------------------
-- 6. Invoice header  (load step 5)
-- ---------------------------------------------------------------------

CREATE TABLE invoice_header (
    invoice_key         UUID PRIMARY KEY,
    natural_key         TEXT NOT NULL,            -- vendor + invoice number
    invoice_number      TEXT NOT NULL,
    invoice_date        DATE,
    due_date            DATE,
    payment_terms       VARCHAR(64),
    po_number           TEXT,
    biller_party_key    UUID NOT NULL REFERENCES party(party_key),
    payer_party_key     UUID REFERENCES party(party_key),
    ship_to_party_key   UUID REFERENCES party(party_key),
    shipment_key        UUID REFERENCES shipment(shipment_key),
    currency_code       CHAR(3) REFERENCES currency(currency_code),
    subtotal            NUMERIC(16,2),
    tax_amount          NUMERIC(16,2),
    freight_amount      NUMERIC(16,2),
    accessorial_total   NUMERIC(16,2),
    discount_amount     NUMERIC(16,2),
    total_amount        NUMERIC(16,2),
    credits_applied     NUMERIC(16,2) NOT NULL DEFAULT 0,
    amount_due          NUMERIC(16,2),
    ack_number          TEXT REFERENCES acknowledgement(ack_number),
    job_number          TEXT REFERENCES job(job_number),
    attribution_method  VARCHAR(48),
    selling_location_key UUID REFERENCES selling_location(selling_location_key),
    -- Validation state carried as data, so an analyst can filter to verified
    -- figures and the accuracy statement stays checkable.
    arithmetic_status   VARCHAR(16) NOT NULL,     -- proved | failed | not_provable
    consensus_flag      consensus_flag_t NOT NULL,
    source_document_id  UUID NOT NULL REFERENCES document(document_id),
    source_page_range   VARCHAR(32),
    source_confidence   NUMERIC(5,4),
    review_status       review_status_t NOT NULL,
    has_handwriting     BOOLEAN NOT NULL DEFAULT FALSE,
    amendment_source    amendment_source_t NOT NULL DEFAULT 'printed',
    batch_id            UUID NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_invoice_natural UNIQUE (natural_key),
    CONSTRAINT ck_invoice_dates CHECK (due_date IS NULL OR invoice_date IS NULL
                                       OR due_date >= invoice_date)
);

CREATE INDEX idx_invoice_biller ON invoice_header(biller_party_key);
CREATE INDEX idx_invoice_payer  ON invoice_header(payer_party_key);
CREATE INDEX idx_invoice_date   ON invoice_header(invoice_date);
CREATE INDEX idx_invoice_number ON invoice_header(invoice_number);
CREATE INDEX idx_invoice_arith  ON invoice_header(arithmetic_status)
    WHERE arithmetic_status <> 'proved';

-- ---------------------------------------------------------------------
-- 7. Invoice line  (load step 6)
-- ---------------------------------------------------------------------

CREATE TABLE invoice_line (
    invoice_line_key    UUID PRIMARY KEY,
    invoice_key         UUID NOT NULL REFERENCES invoice_header(invoice_key)
                            ON DELETE RESTRICT,
    line_number         INTEGER NOT NULL,
    item_key            UUID REFERENCES item(item_key),
    description         TEXT,
    quantity            NUMERIC(16,4),
    uom_code            VARCHAR(16) REFERENCES unit_of_measure(uom_code),
    unit_price          NUMERIC(16,4),
    extended_amount     NUMERIC(16,2),
    discount            NUMERIC(16,2) NOT NULL DEFAULT 0,
    tax_code            VARCHAR(32),
    gl_code             VARCHAR(48),
    cost_centre         VARCHAR(48),
    country_of_origin   CHAR(2) REFERENCES country(country_code),
    ack_number          TEXT REFERENCES acknowledgement(ack_number),
    job_number          TEXT REFERENCES job(job_number),
    attribution_method  VARCHAR(48),
    selling_location_key UUID REFERENCES selling_location(selling_location_key),
    source_confidence   NUMERIC(5,4),
    review_status       review_status_t NOT NULL,
    has_handwriting     BOOLEAN NOT NULL DEFAULT FALSE,
    amendment_source    amendment_source_t NOT NULL DEFAULT 'printed',
    batch_id            UUID NOT NULL,
    CONSTRAINT uq_invoice_line UNIQUE (invoice_key, line_number)
);

-- Accessorials stay itemized. Collapsing them into a single freight figure
-- destroys the leakage analysis, which is usually where the recoverable money
-- is -- and it cannot be recovered later without re-extraction.
CREATE TABLE invoice_accessorial (
    accessorial_key     UUID PRIMARY KEY,
    invoice_key         UUID NOT NULL REFERENCES invoice_header(invoice_key)
                            ON DELETE RESTRICT,
    charge_code         VARCHAR(32) REFERENCES charge_code(charge_code),
    raw_description     TEXT,
    amount              NUMERIC(16,2) NOT NULL,
    ack_number          TEXT REFERENCES acknowledgement(ack_number),
    job_number          TEXT REFERENCES job(job_number),
    attribution_method  VARCHAR(48),
    selling_location_key UUID REFERENCES selling_location(selling_location_key),
    source_confidence   NUMERIC(5,4),
    review_status       review_status_t NOT NULL,
    batch_id            UUID NOT NULL
);

CREATE INDEX idx_accessorial_code ON invoice_accessorial(charge_code);

-- ---------------------------------------------------------------------
-- 8. Payment and application  (load step 7)
--
-- payment carries no invoice_id. Partial payments and one-cheque-many-invoices
-- are the normal case in this data, not an edge case, so the relationship is
-- many-to-many through payment_application.
-- ---------------------------------------------------------------------

CREATE TABLE payment (
    payment_key         UUID PRIMARY KEY,
    natural_key         TEXT NOT NULL,
    payment_date        DATE,
    payer_party_key     UUID REFERENCES party(party_key),
    payee_party_key     UUID REFERENCES party(party_key),
    payment_method      VARCHAR(32),              -- cheque | ach | wire | card
    cheque_number       VARCHAR(48),
    -- Frequently the only link between an invoice and its payment is a cheque
    -- number handwritten on the invoice itself. Flag it so the analytics can
    -- report how much of the matching rests on the least reliable path.
    cheque_number_from_handwriting BOOLEAN NOT NULL DEFAULT FALSE,
    payment_reference   TEXT,
    total_paid          NUMERIC(16,2) NOT NULL,
    currency_code       CHAR(3) REFERENCES currency(currency_code),
    ack_number          TEXT REFERENCES acknowledgement(ack_number),
    job_number          TEXT REFERENCES job(job_number),
    attribution_method  VARCHAR(48),
    selling_location_key UUID REFERENCES selling_location(selling_location_key),
    source_document_id  UUID REFERENCES document(document_id),
    source_confidence   NUMERIC(5,4),
    review_status       review_status_t NOT NULL,
    has_handwriting     BOOLEAN NOT NULL DEFAULT FALSE,
    batch_id            UUID NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_payment_natural UNIQUE (natural_key)
);

CREATE TABLE payment_application (
    application_key     UUID PRIMARY KEY,
    payment_key         UUID NOT NULL REFERENCES payment(payment_key)
                            ON DELETE RESTRICT,
    invoice_key         UUID NOT NULL REFERENCES invoice_header(invoice_key)
                            ON DELETE RESTRICT,
    amount_applied      NUMERIC(16,2) NOT NULL,
    discount_taken      NUMERIC(16,2) NOT NULL DEFAULT 0,
    adjustment_reason   TEXT,
    match_method        match_method_t NOT NULL,
    match_confidence    NUMERIC(5,4),
    ack_number          TEXT REFERENCES acknowledgement(ack_number),
    job_number          TEXT REFERENCES job(job_number),
    attribution_method  VARCHAR(48),
    selling_location_key UUID REFERENCES selling_location(selling_location_key),
    review_status       review_status_t NOT NULL,
    batch_id            UUID NOT NULL,
    CONSTRAINT uq_payment_application UNIQUE (payment_key, invoice_key)
);

CREATE INDEX idx_application_invoice ON payment_application(invoice_key);
CREATE INDEX idx_application_method  ON payment_application(match_method);

-- One row accounts for each monetary target. A missing ACK/job is permitted
-- only with an explicit reason code and amount; unresolved dollars stay open.
CREATE TABLE attribution (
    attribution_id      UUID PRIMARY KEY,
    target_table        VARCHAR(64) NOT NULL,
    target_key          UUID NOT NULL,
    amount              NUMERIC(16,2) NOT NULL,
    currency_code       CHAR(3) REFERENCES currency(currency_code),
    ack_number          TEXT REFERENCES acknowledgement(ack_number),
    job_number          TEXT REFERENCES job(job_number),
    attribution_rank    INTEGER,
    attribution_method  VARCHAR(48),
    attribution_confidence NUMERIC(5,4),
    evidence_chain      JSONB NOT NULL DEFAULT '[]'::jsonb,
    reason_code         VARCHAR(48),
    is_failure          BOOLEAN NOT NULL DEFAULT FALSE,
    selling_location_key UUID REFERENCES selling_location(selling_location_key),
    selling_location_method VARCHAR(48),
    source_document_id  UUID NOT NULL REFERENCES document(document_id),
    review_status       review_status_t NOT NULL,
    batch_id            UUID NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_attribution_rank CHECK (
        attribution_rank IS NULL OR attribution_rank BETWEEN 1 AND 8
    ),
    CONSTRAINT ck_attribution_accounted CHECK (
        ack_number IS NOT NULL OR job_number IS NOT NULL OR reason_code IS NOT NULL
    )
);

CREATE INDEX idx_attribution_target ON attribution(target_table, target_key);
CREATE INDEX idx_attribution_ack ON attribution(ack_number);
CREATE INDEX idx_attribution_job ON attribution(job_number);

-- Detected annotations remain linked evidence even when recognition is
-- intentionally descoped. A region record is never an approved amendment.
CREATE TABLE handwriting_region (
    region_id           UUID PRIMARY KEY,
    source_region_id    TEXT,
    document_id         UUID NOT NULL REFERENCES document(document_id),
    page_id             TEXT NOT NULL,
    semantic_type       VARCHAR(48),
    content_class       VARCHAR(24),
    left_coord          NUMERIC(7,6),
    top_coord           NUMERIC(7,6),
    right_coord         NUMERIC(7,6),
    bottom_coord        NUMERIC(7,6),
    crop_path           TEXT,
    transcription      TEXT,
    transcription_status VARCHAR(32) NOT NULL,
    detector_engine     TEXT,
    recognizer_evidence JSONB,
    review_status       review_status_t NOT NULL,
    batch_id            UUID NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_handwriting_region_coords CHECK (
        (left_coord IS NULL AND top_coord IS NULL AND right_coord IS NULL AND bottom_coord IS NULL)
        OR (left_coord BETWEEN 0 AND 1 AND top_coord BETWEEN 0 AND 1
            AND right_coord BETWEEN 0 AND 1 AND bottom_coord BETWEEN 0 AND 1
            AND left_coord < right_coord AND top_coord < bottom_coord)
    )
);

-- ---------------------------------------------------------------------
-- 9. Amendment  (load step 9)
--
-- The table that encodes the precedence rule: printed values are the baseline,
-- handwritten values are amendments with a later effective time, and BOTH are
-- stored. The canonical layer exposes amended_value as current and retains
-- original_value. Nothing is overwritten.
-- ---------------------------------------------------------------------

CREATE TABLE amendment (
    amendment_id        UUID PRIMARY KEY,
    target_table        VARCHAR(64) NOT NULL,
    target_key          UUID NOT NULL,
    target_line         INTEGER,
    target_column       VARCHAR(64) NOT NULL,
    original_value      TEXT,
    amended_value       TEXT NOT NULL,
    value_delta         NUMERIC(16,2),            -- populated for numeric columns
    amendment_source    amendment_source_t NOT NULL,
    annotation_type     VARCHAR(48),              -- quantity_correction | price_override |
                                                  -- cheque_number | delivery_date |
                                                  -- damage_note | gl_coding | signature
    effective_time      TIMESTAMPTZ NOT NULL,
    evidence_document_id UUID NOT NULL REFERENCES document(document_id),
    evidence_region     VARCHAR(64),              -- bounding box on the page
    recognition_agreement VARCHAR(16),            -- 3of3 | 2of3 | escalated
    reviewed_by         TEXT,
    reviewed_at         TIMESTAMPTZ,
    review_status       review_status_t NOT NULL,
    batch_id            UUID NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_amendment_target ON amendment(target_table, target_key);
CREATE INDEX idx_amendment_source ON amendment(amendment_source);
CREATE INDEX idx_amendment_unreviewed ON amendment(reviewed_at)
    WHERE reviewed_at IS NULL;

-- Amendments are append-only. Fail loudly on mutation so an attempted change
-- cannot be mistaken for a successful audit-trail update.
CREATE FUNCTION reject_amendment_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'amendment rows are append-only; create a new amendment';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER amendment_reject_mutation
    BEFORE UPDATE OR DELETE ON amendment
    FOR EACH ROW EXECUTE FUNCTION reject_amendment_mutation();

-- ---------------------------------------------------------------------
-- 10. Exception event  (load step 9)
-- ---------------------------------------------------------------------

CREATE TABLE exception_event (
    exception_id        UUID PRIMARY KEY,
    shipment_key        UUID REFERENCES shipment(shipment_key),
    document_id         UUID REFERENCES document(document_id),
    invoice_key         UUID REFERENCES invoice_header(invoice_key),
    event_type          VARCHAR(48) NOT NULL,     -- damage | shortage | refusal |
                                                  -- no_consensus | arithmetic_failure |
                                                  -- unmatched_payment | sequence_gap
    severity            VARCHAR(16),
    description         TEXT,
    from_handwriting    BOOLEAN NOT NULL DEFAULT FALSE,
    financial_impact    NUMERIC(16,2),
    resolved            BOOLEAN NOT NULL DEFAULT FALSE,
    resolved_at         TIMESTAMPTZ,
    resolved_by         TEXT,
    resolution_note     TEXT,
    review_status       review_status_t NOT NULL,
    batch_id            UUID NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_exception_open ON exception_event(resolved) WHERE NOT resolved;

-- ---------------------------------------------------------------------
-- 11. Financial event stream
--
-- Every document normalizes to a ledger-shaped event. Non-financial documents
-- (BOL, packing list) register with a NULL amount so the shipment lifecycle
-- stays traceable rather than disappearing from the stream.
-- ---------------------------------------------------------------------

CREATE TABLE financial_event (
    event_id            UUID PRIMARY KEY,
    event_type          event_type_t NOT NULL,
    party_key           UUID REFERENCES party(party_key),
    amount              NUMERIC(16,2),            -- NULL for non-financial documents
    currency_code       CHAR(3) REFERENCES currency(currency_code),
    effective_date      DATE NOT NULL,
    source_document_id  UUID NOT NULL REFERENCES document(document_id),
    invoice_key         UUID REFERENCES invoice_header(invoice_key),
    payment_key         UUID REFERENCES payment(payment_key),
    shipment_key        UUID REFERENCES shipment(shipment_key),
    ack_number          TEXT REFERENCES acknowledgement(ack_number),
    job_number          TEXT REFERENCES job(job_number),
    attribution_method  VARCHAR(48),
    selling_location_key UUID REFERENCES selling_location(selling_location_key),
    review_status       review_status_t NOT NULL,
    batch_id            UUID NOT NULL
);

CREATE INDEX idx_event_period ON financial_event(effective_date, event_type);
CREATE INDEX idx_event_party  ON financial_event(party_key);

-- ---------------------------------------------------------------------
-- 12. Load manifest
--
-- Compare against the previous manifest on every re-run. Corrected batches
-- being re-run is the normal operating mode, not an exception.
-- ---------------------------------------------------------------------

CREATE TABLE load_manifest (
    batch_id            UUID NOT NULL,
    loaded_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    load_step           INTEGER NOT NULL,
    entity              VARCHAR(64) NOT NULL,
    records_attempted   INTEGER NOT NULL,
    records_loaded      INTEGER NOT NULL,
    records_rejected    INTEGER NOT NULL,
    reject_causes       JSONB,
    checksum            VARCHAR(64),
    completeness_pct    NUMERIC(5,2),
    accuracy_upper_bound NUMERIC(16,2),
    notes               TEXT,
    PRIMARY KEY (batch_id, load_step, entity)
);

-- ---------------------------------------------------------------------
-- 13. Convenience views
-- ---------------------------------------------------------------------

-- Current values with amendments applied. Query this, not invoice_line
-- directly, wherever the operative figure is wanted.
CREATE VIEW v_invoice_line_current AS
SELECT
    l.*,
    COALESCE(
        (SELECT a.amended_value::NUMERIC
           FROM amendment a
          WHERE a.target_table = 'invoice_line'
            AND a.target_key = l.invoice_line_key
            AND a.target_column = 'quantity'
          ORDER BY a.effective_time DESC LIMIT 1),
        l.quantity
    ) AS quantity_current,
    EXISTS (SELECT 1 FROM amendment a
             WHERE a.target_table = 'invoice_line'
               AND a.target_key = l.invoice_line_key) AS is_amended
FROM invoice_line l;

-- Trust surface. Every figure displayed in the CRM should be able to reach
-- this view, so a user can see which numbers are verified.
CREATE VIEW v_document_trust AS
SELECT
    d.document_id,
    d.document_type,
    d.source_file,
    d.source_page_range,
    d.branch,
    d.has_handwriting,
    d.jbig2_suspect,
    d.consensus_flag,
    d.arithmetic_status,
    d.review_status,
    (d.review_status IN ('auto_accepted', 'sampled_verified', 'exception_resolved')
     AND d.arithmetic_status = 'proved') AS is_trusted
FROM document d;

COMMIT;

-- =====================================================================
-- Integrity rules the CRM application must enforce
-- (restated in references/data-model.md -- easy to lose in translation,
--  expensive to retrofit)
--
--   1. Party merges remain reversible. Never hard-delete a merged party;
--      set merged_into and keep party_name_variant.
--   2. Amendment rows are append-only.
--   3. The raw layer is never written to by the application.
--   4. Any document ingested through the CRM follows the same consensus and
--      arithmetic-proof rules, or the historical dataset's accuracy statement
--      stops applying to the combined data.
--   5. review_status is preserved on load and surfaced in the UI wherever a
--      figure is displayed.
--   6. All loads are idempotent upsert by natural_key.
--   7. A failed foreign key halts the load. It does not silently drop the row --
--      a silently dropped row becomes a missing invoice nobody notices until a
--      reconciliation fails months later.
-- =====================================================================
