# Allocation Policy and Sales-Credit Controls

This runbook applies to commission reports and any future data layer that affects
sales credit, compensation, allocation, eligibility, or reporting treatment.
It is a **policy layer**, not a column-renaming rule. It preserves each reported
amount, rate, share, description, and source reference before deriving an
allocation result.

## Design: flexible without silent assumptions

Every normalized allocation leg may carry a `dimensions` object for approved
source concepts, plus common convenience fields such as `brand_name`,
`dealer_name`, `product_name`, `product_sku`, `client_id`,
`selling_location`, and `effective_date`. New concepts are not hard-coded into
the program. They first enter semantic schema discovery as a proposed mapping or
schema change; after client approval they can be carried in `dimensions` and
used to scope a policy.

A policy is selected only when all of these match:

1. The exact ordered report-template fingerprint.
2. Every configured dimension (for example brand, dealer, product, client, or
   a future approved concept).
3. The inclusive `effective_from` / `effective_to` date window, when present.

The most-specific matching rule wins. A tie, absent rule, invalid date, unknown
concept, missing monetary evidence, or failed formula is review work. The
registry is append-only: changing a percentage or rule creates a later snapshot
with its own scope and effective date; it never changes history.

## Commission rule implemented for the initial policy

For each allocation leg:

```text
effective_commission_rate = commission_amount / commissionable_amount
```

The policy can use the reported nominal rate and allocation share to prove:

```text
expected_commission_amount = commissionable_amount × stated_rate × allocation_share
```

With an approved scoped policy, an explicit `ship-to` indicator takes priority
when configured. Otherwise a rate at or below the policy’s effective-rate
threshold is classified `administrative_ship_to` and excluded from sales credit.
A rate above the threshold can be `sales_generated` only if that exact approved
policy permits it. The threshold is not global or baked into code: it belongs to
a versioned policy rule and can differ by brand, dealer, product, client,
location, or dates. Any formula mismatch goes to final review.

## LLM role and unknown layers

The LLM may inspect an unfamiliar report layout only through strict structured
output. It proposes allocation roles and the policy controls that might apply;
it cannot approve a policy, add a canonical field, set a sales-credit result, or
rewrite a registry. The operational sequence for any new layer is:

1. Retain its original page/row and exact labels.
2. Use `schema_discovery.py` to map known concepts and queue unknown concepts.
3. Use `allocation_policy.py discover` to create a policy proposal for the
   report layout.
4. Produce the client decision return file.
5. Apply only client-approved decisions to create the next registry snapshot.
6. Run the allocation policy; unresolved rows remain in the final review gate.

This lets the model help interpret new layers as they arrive while preserving a
clear distinction between a suggestion, a client-approved policy, and a derived
business result.

## Client review: the only file to edit

Do **not** edit `allocation_policy_proposals.json` or
`approved_allocation_policy_registry.json`. They are audit artifacts.

Create the concise return file:

```bash
python scripts/allocation_policy.py decision-template \
  allocation_policy_proposals.json --out allocation_policy_client_decisions.json
```

The client edits only `decisions[]` in
`allocation_policy_client_decisions.json`:

- `decision`: use `approve` or leave `defer`.
- `scope.dimensions`: specify where the policy applies, such as
  `{ "brand_name": "Sample Brand", "product_sku": "SKU-100" }`.
  Any approved source dimension is valid.
- `scope.effective_from` / `scope.effective_to`: use ISO dates when the policy
  changes over time; omit them when it does not.
- `policy_override`: leave `{}` to accept the proposal, or explicitly provide
  only a replacement `effective_rate_threshold`,
  `explicit_ship_to_precedence`, or `allow_sales_generated` value.
- `client_comment`: record the decision rationale or supporting reference.

One decision file can contain multiple cards for the same template when a rule
varies by brand, dealer, product, client, geography, or date. The safe default
is `defer`; empty or invalid values create no registry rule.

Build the next snapshot only after the completed return file is received:

```bash
python scripts/allocation_policy.py registry-update \
  approved_allocation_policy_registry.json allocation_policy_proposals.json \
  allocation_policy_client_decisions.json \
  --out approved_allocation_policy_registry_next.json
```

## Commands and artifacts

`allocation_templates.json` is the source-template observation artifact built by
`scripts/template_observations.py` from a completed consensus run. `discover`
reads that; the attributed records belong to `apply`.

```bash
# Proposal-only LLM analysis of an unfamiliar commission report layout.
python scripts/allocation_policy.py discover allocation_templates.json --enable \
  --out allocation_policy_proposals.json \
  --exceptions allocation_policy_exceptions.json \
  --handoff-out allocation_policy_handoff.json --raw-dir allocation_policy_raw
python scripts/operations.py adapter allocation_policy_handoff.json \
  --type allocation_policy --credential-env OPENAI_API_KEY \
  --out allocation_policy_adapter_contract.json

# Apply only the approved, versioned policy to evidence-bearing allocation legs.
python scripts/allocation_policy.py apply allocation_legs.json \
  --registry approved_allocation_policy_registry_next.json \
  --out allocation_results.json --exceptions allocation_exceptions.json

# Include the exception artifact in the standard exhaustive final-review gate.
python scripts/final_review_queue.py allocation_exceptions.json \
  --out allocation_final_review.json
python scripts/operations.py review-export allocation_final_review.json \
  --csv allocation_final_review.csv --html allocation_final_review.html \
  --xlsx allocation_final_review.xlsx
```

The compact final-review JSON/CSV/XLSX tells a client only what needs attention.
The completed decision return file is the explicit instruction to update a
registry. The source report, raw LLM response, proposal, decision file, and
registry snapshot all remain linked evidence.

## Google Places setup

Google Places is optional and separate from allocation policy. It helps find
candidate dealer/brand locations from an observed name and city clue; it never
proves entity identity or determines sales credit.

1. In the same Google Cloud project that owns the billing account, enable
   **Places API (New)**. Keep **Address Validation API** enabled for the separate
   source-address validation step.
2. Create a dedicated server-side key (or use an existing dedicated key) and
   restrict its API access to **Places API (New)** and, if shared, **Address
   Validation API**. Do not enable unrelated Maps products.
3. Apply the appropriate application restriction: use a stable server egress IP
   restriction after deployment. Do not put this key in a browser or client app.
4. In the Git-ignored root `.env`, set the key once and enable only the desired
   feature:

   ```dotenv
   GOOGLE_PLACES_API_KEY=your_separate_places_key
   GOOGLE_PLACES_ENABLED=true
   GOOGLE_PLACES_API_KEY_ENV=GOOGLE_PLACES_API_KEY
   GOOGLE_PLACES_MAX_REQUESTS=100
   GOOGLE_PLACES_TIMEOUT_SECONDS=15
   ```

5. Retain the default cap until observed volume and cost are understood. The
   implementation sends a POST Text Search request with a narrow field mask and
   records candidate responses as review-required evidence.

Text Search (New) requires a field mask, and Google does not guarantee identical
candidate results for identical searches. Use the source page and client review
to select a location; use Address Validation only when an actual source address
is available.
