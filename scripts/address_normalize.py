#!/usr/bin/env python3
"""Derive CRM-ready address components and optional Google validation evidence.

The offline parser always preserves extracted address evidence. Google Maps
Address Validation is an explicit, capped opt-in enrichment: it never replaces
source values or local components, and it routes any uncertainty to review.
"""

import argparse
import json
import os
import re
import sys
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from cli_help import apply_shared_help
from llm_runtime import retry_call, retryable_error
from runtime_config import env_bool, env_float, env_int, env_value, load_project_env

ADDRESS_FIELDS = (
    "seller_address",
    "buyer_address",
    "ship_to_address",
    "remit_to_address",
    "vendor_address",
    "origin_address",
    "destination_address",
)
COUNTRY_CODES = {
    "CANADA": "CA",
    "CA": "CA",
    "UNITED STATES": "US",
    "UNITED STATES OF AMERICA": "US",
    "USA": "US",
    "US": "US",
    "UNITED KINGDOM": "GB",
    "UK": "GB",
    "GREAT BRITAIN": "GB",
    "GB": "GB",
    "AUSTRALIA": "AU",
    "AU": "AU",
    "BRAZIL": "BR",
    "BR": "BR",
    "CHINA": "CN",
    "CN": "CN",
    "FRANCE": "FR",
    "FR": "FR",
    "GERMANY": "DE",
    "DE": "DE",
    "HONG KONG": "HK",
    "HK": "HK",
    "INDIA": "IN",
    "IN": "IN",
    "IRELAND": "IE",
    "IE": "IE",
    "JAPAN": "JP",
    "JP": "JP",
    "MEXICO": "MX",
    "MX": "MX",
    "NETHERLANDS": "NL",
    "NL": "NL",
    "NEW ZEALAND": "NZ",
    "NZ": "NZ",
    "SINGAPORE": "SG",
    "SG": "SG",
    "THAILAND": "TH",
    "TH": "TH",
}
US_STATE_CODES = {
    "AL",
    "AK",
    "AZ",
    "AR",
    "CA",
    "CO",
    "CT",
    "DE",
    "FL",
    "GA",
    "HI",
    "IA",
    "ID",
    "IL",
    "IN",
    "KS",
    "KY",
    "LA",
    "MA",
    "MD",
    "ME",
    "MI",
    "MN",
    "MO",
    "MS",
    "MT",
    "NC",
    "ND",
    "NE",
    "NH",
    "NJ",
    "NM",
    "NV",
    "NY",
    "OH",
    "OK",
    "OR",
    "PA",
    "RI",
    "SC",
    "SD",
    "TN",
    "TX",
    "UT",
    "VA",
    "VT",
    "WA",
    "WI",
    "WV",
    "WY",
    "DC",
    # Territories that use the US ZIP system and therefore parse as US addresses.
    "PR",
    "VI",
    "GU",
    "AS",
    "MP",
}
# Canada is a first-class supported country here -- it has a postal pattern and a
# country code -- but without its province codes every Canadian province landed in
# the city field and state_or_region was always null. These are recognized only
# once the country already resolves to CA, so no US address is reinterpreted.
CA_PROVINCE_CODES = {
    "AB",
    "BC",
    "MB",
    "NB",
    "NL",
    "NS",
    "NT",
    "NU",
    "ON",
    "PE",
    "QC",
    "SK",
    "YT",
}
US_POSTAL = re.compile(r"\b\d{5}(?:-\d{4})?\b$")
CA_POSTAL = re.compile(r"\b[A-Z]\d[A-Z][ -]?\d[A-Z]\d\b$", re.I)
UK_POSTAL = re.compile(r"\b[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}\b$", re.I)
SG_POSTAL = re.compile(r"\b\d{6}\b$")
CN_POSTAL = re.compile(r"\b\d{6}\b$")
TH_POSTAL = re.compile(r"\b\d{5}\b$")
# Only these countries have a postal pattern here, so only these can be judged
# against a declared country. France and Germany also use five digits; without a
# pattern for them there is no basis to call a five-digit code a mismatch.
POSTAL_PATTERN_COUNTRIES = frozenset({"US", "CA", "GB", "SG", "CN", "TH"})
GOOGLE_ADDRESS_VALIDATION_ENDPOINT = "https://addressvalidation.googleapis.com/v1:validateAddress"
GOOGLE_FREE_MONTHLY_CAP = 5000
PREMISE_GRANULARITIES = {"PREMISE", "SUB_PREMISE"}
GOOGLE_FAILURE_CATEGORIES = {
    "unsupported_region",
    "permission_denied",
    "request_rejected",
    "failed",
}


def scalar(value):
    """Read a field object without replacing the original object."""
    return value.get("value") if isinstance(value, dict) else value


def field(value):
    """Create a derived field with explicit system provenance."""
    return {"value": value, "confidence": None, "source": "system"}


def address_role(source_field):
    """Convert a raw source field name into a stable CRM-address prefix."""
    return source_field.removesuffix("_address")


def address_parts(raw):
    """Split a freeform address into nonempty comma or newline components."""
    return [part.strip() for part in re.split(r"[,\n]+", raw) if part.strip()]


def country_from(parts):
    """Remove and return a recognized explicit country suffix when present."""
    if not parts:
        return None
    country = COUNTRY_CODES.get(parts[-1].upper())
    if country:
        parts.pop()
    return country


def postal_from(text, country_code=None):
    """Return a supported postal-code form and implied country, if recognizable."""
    text = text.strip()
    patterns = ((US_POSTAL, "US"), (CA_POSTAL, "CA"), (UK_POSTAL, "GB"))
    if country_code:
        country_patterns = {
            **{country: pattern for pattern, country in patterns},
            "SG": SG_POSTAL,
            "CN": CN_POSTAL,
            "TH": TH_POSTAL,
        }
        patterns = tuple((pattern, country) for country, pattern in country_patterns.items())
    for pattern, country in patterns:
        if country_code and country != country_code:
            continue
        match = pattern.search(text)
        if match:
            return match.group(0).upper().replace(" ", ""), country, match.start()
    return None, None, None


def parse_address(raw):
    """Return parsed components and conservative format-review reasons."""
    parts = address_parts(raw)
    parsed = {
        "address_line1": parts[0] if parts else None,
        "address_line2": None,
        "city": None,
        "state_or_region": None,
        "postal_code": None,
        "country_code": None,
    }
    country = country_from(parts)
    if country:
        parsed["country_code"] = country
    if not parts:
        return parsed, ["address_parse_incomplete"]

    last = parts[-1]
    declared_country = parsed["country_code"]
    postal, implied_country, postal_start = postal_from(last, declared_country)
    country_postal_mismatch = False
    if postal:
        parsed["postal_code"] = postal
        parsed["country_code"] = parsed["country_code"] or implied_country
        last = last[:postal_start].strip(" ,")
    elif declared_country in POSTAL_PATTERN_COUNTRIES:
        # The declared country's own pattern did not match. If a different
        # supported country's pattern does, the address contradicts itself.
        # Scoped matching would otherwise drop the postal code silently and leave
        # it sitting in the city field with a clean format verdict. A country
        # without a pattern here is never judged, because a five-digit French or
        # German code is not evidence of anything.
        foreign, foreign_country, _ = postal_from(last)
        if foreign and foreign_country != declared_country:
            country_postal_mismatch = True

    state_match = re.search(r"\b([A-Z]{2})\b$", last)
    if state_match and state_match.group(1) in US_STATE_CODES:
        parsed["state_or_region"] = state_match.group(1)
        parsed["country_code"] = parsed["country_code"] or "US"
        last = last[: state_match.start()].strip(" ,")
    elif (
        state_match and parsed["country_code"] == "CA" and state_match.group(1) in CA_PROVINCE_CODES
    ):
        parsed["state_or_region"] = state_match.group(1)
        last = last[: state_match.start()].strip(" ,")
    if last:
        parsed["city"] = last
        parts.pop()
    elif len(parts) > 1:
        parts.pop()
        # "1 Main St, Springfield, IL, 62704" puts the region in its own comma
        # segment. Taking the next segment as the city unconditionally made the
        # region the city, emptied state_or_region, and reported no review reason
        # -- a garbage city then flows into entity resolution and CRM staging.
        if parsed["state_or_region"] is None and parts:
            candidate = parts[-1].strip().upper()
            if candidate in US_STATE_CODES:
                parsed["state_or_region"] = candidate
                parsed["country_code"] = parsed["country_code"] or "US"
                parts.pop()
            elif parsed["country_code"] == "CA" and candidate in CA_PROVINCE_CODES:
                parsed["state_or_region"] = candidate
                parts.pop()
        if parts:
            parsed["city"] = parts.pop()

    if parts:
        parsed["address_line1"] = parts[0]
        if len(parts) > 1:
            parsed["address_line2"] = ", ".join(parts[1:])
    reasons = []
    if not parsed["address_line1"] or not parsed["city"]:
        reasons.append("address_parse_incomplete")
    if country_postal_mismatch:
        reasons.append("address_country_postal_mismatch")
    return parsed, reasons


def issue(document_id, source_field, reason, value=None):
    """Make a stable exception consumable by the final client-review gate."""
    return {
        "document_id": document_id,
        "field": f"header.{source_field}",
        "reason": reason,
        "value": value,
        "disposition": "client_review_required",
    }


def normalize_record(record):
    """Append local parsed-address evidence and fields without overwriting source values."""
    copy = deepcopy(record)
    header = copy.get("header")
    if header is None:
        header = copy
    if not isinstance(header, dict):
        raise ValueError("Record header must be an object")
    normalizations, exceptions = [], []
    document_id = copy.get("document_id", "unknown")
    for source_field in ADDRESS_FIELDS:
        raw = scalar(header.get(source_field))
        if raw is None or not str(raw).strip():
            continue
        role = address_role(source_field)
        parsed, reasons = parse_address(str(raw).strip())
        derived = {f"{role}_{name}": field(value) for name, value in parsed.items()}
        conflicts = []
        for name, value in derived.items():
            if value["value"] is None:
                continue
            if name not in header:
                header[name] = value
            elif scalar(header[name]) != value["value"]:
                conflicts.append(name)
                exceptions.append(
                    issue(document_id, name, "address_component_conflict", value["value"])
                )
        for reason in reasons:
            exceptions.append(issue(document_id, source_field, reason, raw))
        normalizations.append(
            {
                "source_field": source_field,
                "raw_value": raw,
                "derived_fields": derived,
                "format_validation_status": "format_valid" if not reasons else "review_required",
                "external_validation_status": "not_requested",
                "conflicting_existing_fields": conflicts,
            }
        )
    copy["address_normalizations"] = normalizations
    copy["address_validation_status"] = "clear" if not exceptions else "client_review_required"
    return copy, exceptions


def google_payload(raw_value, parsed, enable_usps_cass):
    """Build a validation request from unchanged source evidence and a region hint."""
    address = {"addressLines": [str(raw_value)]}
    if parsed.get("country_code"):
        address["regionCode"] = parsed["country_code"]
    return {
        "address": address,
        "enableUspsCass": bool(enable_usps_cass and parsed.get("country_code") in {"PR", "US"}),
    }


def google_error_category(error):
    """Map a provider HTTP failure to a non-secret review category."""
    try:
        body = json.loads(error.read().decode())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        body = {}
    details = body.get("error") if isinstance(body, dict) else None
    details = details if isinstance(details, dict) else {}
    status, message = details.get("status"), details.get("message")
    if isinstance(message, str) and message.startswith("Unsupported region code"):
        return "unsupported_region"
    if status == "PERMISSION_DENIED":
        return "permission_denied"
    if status == "INVALID_ARGUMENT":
        return "request_rejected"
    return "failed"


def google_request(payload, api_key, timeout_seconds, opener=urlopen):
    """Call Google's Address Validation endpoint without retaining the API key."""
    request = Request(
        GOOGLE_ADDRESS_VALIDATION_ENDPOINT,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "X-Goog-Api-Key": api_key},
        method="POST",
    )
    try:
        with opener(request, timeout=timeout_seconds) as response:
            data = json.loads(response.read().decode())
    except HTTPError as exc:
        raise RuntimeError(google_error_category(exc)) from exc
    except (URLError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("failed") from exc
    if not isinstance(data, dict) or not isinstance(data.get("result"), dict):
        raise RuntimeError("Google Address Validation response did not contain a result object")
    return data


def google_evidence(response, role):
    """Reduce provider data to durable evidence and explicitly named CRM proposals."""
    result = response["result"]
    verdict = result.get("verdict") if isinstance(result.get("verdict"), dict) else {}
    address = result.get("address") if isinstance(result.get("address"), dict) else {}
    postal = address.get("postalAddress") if isinstance(address.get("postalAddress"), dict) else {}
    lines = postal.get("addressLines") if isinstance(postal.get("addressLines"), list) else []
    values = {
        "address_line1": lines[0] if lines else None,
        "address_line2": lines[1] if len(lines) > 1 else None,
        "city": postal.get("locality"),
        "state_or_region": postal.get("administrativeArea"),
        "postal_code": postal.get("postalCode"),
        "country_code": postal.get("regionCode"),
    }
    geocode = result.get("geocode") if isinstance(result.get("geocode"), dict) else {}
    location = geocode.get("location") if isinstance(geocode.get("location"), dict) else {}
    provider_fields = {
        f"{role}_validated_{name}": field(value)
        for name, value in values.items()
        if value is not None
    }
    for key, name in (("latitude", "geocode_latitude"), ("longitude", "geocode_longitude")):
        if location.get(key) is not None:
            provider_fields[f"{role}_{name}"] = field(location[key])
    if geocode.get("placeId"):
        provider_fields[f"{role}_google_place_id"] = field(geocode["placeId"])
    address_complete = verdict.get("addressComplete") is True
    validation_granularity = verdict.get("validationGranularity")
    geocode_granularity = verdict.get("geocodeGranularity")
    uncertainty = any(
        verdict.get(flag) is True
        for flag in (
            "hasUnconfirmedComponents",
            "hasInferredComponents",
            "hasReplacedComponents",
            "hasUnresolvedTokens",
        )
    )
    validated = (
        address_complete
        and validation_granularity in PREMISE_GRANULARITIES
        and geocode_granularity in PREMISE_GRANULARITIES
        and not uncertainty
    )
    evidence = {
        "provider": "google_maps_address_validation",
        "response_id": response.get("responseId"),
        "status": "validated" if validated else "review_required",
        "verdict": {
            "address_complete": address_complete,
            "validation_granularity": validation_granularity,
            "geocode_granularity": geocode_granularity,
            "has_unconfirmed_components": verdict.get("hasUnconfirmedComponents") is True,
            "has_inferred_components": verdict.get("hasInferredComponents") is True,
            "has_replaced_components": verdict.get("hasReplacedComponents") is True,
            "has_unresolved_tokens": verdict.get("hasUnresolvedTokens") is True,
            "possible_next_action": verdict.get("possibleNextAction"),
        },
        "formatted_address": address.get("formattedAddress"),
        "provider_derived_fields": provider_fields,
        "usps_data_present": isinstance(result.get("uspsData"), dict),
    }
    return evidence


def add_provider_fields(header, provider_fields):
    """Append validated proposals only when no existing field would be overwritten."""
    conflicts = []
    for name, value in provider_fields.items():
        if name not in header:
            header[name] = value
        elif scalar(header[name]) != value["value"]:
            conflicts.append(name)
    return conflicts


def apply_google_validation(records, api_key, max_requests, timeout_seconds, enable_usps_cass):
    """Validate unique addresses up to the explicit per-run cap and collect review work."""
    cache, exceptions, requests = {}, [], 0
    for record in records:
        record_exception_count = len(exceptions)
        header = record.get("header", record)
        document_id = record.get("document_id", "unknown")
        for normalization in record["address_normalizations"]:
            raw_value = normalization["raw_value"]
            parsed = {
                name.removeprefix(f"{address_role(normalization['source_field'])}_"): scalar(value)
                for name, value in normalization["derived_fields"].items()
            }
            cache_key = (str(raw_value), parsed.get("country_code"), enable_usps_cass)
            if cache_key not in cache:
                if requests >= max_requests:
                    normalization["external_validation_status"] = "not_requested_limit_reached"
                    exceptions.append(
                        issue(
                            document_id,
                            normalization["source_field"],
                            "google_address_validation_limit_reached",
                            raw_value,
                        )
                    )
                    continue
                requests += 1
                try:
                    cache[cache_key] = {
                        "response": retry_call(
                            lambda raw_value=raw_value, parsed=parsed: google_request(
                                google_payload(raw_value, parsed, enable_usps_cass),
                                api_key,
                                timeout_seconds,
                            ),
                            3,
                            1.0,
                            jitter=True,
                            max_backoff_seconds=30.0,
                            retryable=lambda error: (
                                retryable_error(error) or str(error) == "failed"
                            ),
                        )
                    }
                except RuntimeError as exc:
                    category = str(exc)
                    cache[cache_key] = {
                        "error_category": category
                        if category in GOOGLE_FAILURE_CATEGORIES
                        else "failed"
                    }
            cached_result = cache[cache_key]
            if "error_category" in cached_result:
                category = cached_result["error_category"]
                normalization["external_validation_status"] = category
                normalization["google_address_validation"] = {
                    "status": category,
                    "provider_error_category": category,
                }
                exceptions.append(
                    issue(
                        document_id,
                        normalization["source_field"],
                        f"google_address_validation_{category}",
                        raw_value,
                    )
                )
                continue
            evidence = google_evidence(
                cached_result["response"], address_role(normalization["source_field"])
            )
            normalization["google_address_validation"] = evidence
            normalization["external_validation_status"] = evidence["status"]
            if evidence["status"] != "validated":
                exceptions.append(
                    issue(
                        document_id,
                        normalization["source_field"],
                        "google_address_validation_review_required",
                        raw_value,
                    )
                )
                continue
            conflicts = add_provider_fields(header, evidence["provider_derived_fields"])
            if conflicts:
                normalization["google_component_conflicts"] = conflicts
                exceptions.extend(
                    issue(document_id, name, "google_address_validation_component_conflict")
                    for name in conflicts
                )
        if len(exceptions) > record_exception_count:
            record["address_validation_status"] = "client_review_required"
    return exceptions, requests


def records_from(path):
    """Read a record, list, or normal pipeline document artifact."""
    data = json.loads(Path(path).read_text())
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        if isinstance(data.get("documents"), list):
            return data["documents"]
        if "document_id" in data:
            return [data]
    raise ValueError("Input must be a record, a list of records, or contain documents")


def normalize(records):
    """Carry all records forward and separately collect address-review work."""
    normalized, exceptions = [], []
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("Every record must be an object")
        result, findings = normalize_record(record)
        normalized.append(result)
        exceptions.extend(findings)
    return normalized, exceptions


def main():
    try:
        load_project_env()
        defaults = {
            "google_address_validation": env_bool("GOOGLE_ADDRESS_VALIDATION_ENABLED", False),
            "google_api_key_env": env_value("GOOGLE_MAPS_API_KEY_ENV", "GOOGLE_MAPS_API_KEY"),
            "max_google_requests": env_int("GOOGLE_MAX_REQUESTS", 5000),
            "google_timeout_seconds": env_float("GOOGLE_TIMEOUT_SECONDS", 15.0),
            "google_usps_cass": env_bool("GOOGLE_USPS_CASS_ENABLED", True),
        }
    except ValueError as exc:
        sys.exit(f"Address normalization failed: {exc}")
    parser = argparse.ArgumentParser(
        description="Derive CRM address components and optional Google validation evidence."
    )
    parser.add_argument("input", help="validated JSON, a record, or a list of records")
    parser.add_argument("--out", required=True, help="records with address-normalization evidence")
    parser.add_argument("--exceptions", required=True, help="address findings for client review")
    parser.add_argument(
        "--google-address-validation",
        action=argparse.BooleanOptionalAction,
        default=defaults["google_address_validation"],
        help="explicitly send addresses to Google Maps Address Validation",
    )
    parser.add_argument(
        "--google-api-key-env",
        default=defaults["google_api_key_env"],
        help="environment-variable name containing the Google Maps API key",
    )
    parser.add_argument(
        "--max-google-requests",
        type=int,
        default=defaults["max_google_requests"],
        help=f"explicit per-run cap from 1 to {GOOGLE_FREE_MONTHLY_CAP}; default 0 disables calls",
    )
    parser.add_argument(
        "--google-timeout-seconds",
        type=float,
        default=defaults["google_timeout_seconds"],
        help="Per-request timeout, in seconds, for the Address Validation call.",
    )
    parser.add_argument(
        "--google-usps-cass",
        action=argparse.BooleanOptionalAction,
        default=defaults["google_usps_cass"],
        help="request USPS CASS evidence for US/PR addresses",
    )
    parser.add_argument("--quiet", action="store_true")
    apply_shared_help(parser)
    args = parser.parse_args()
    try:
        if args.google_address_validation:
            if not 1 <= args.max_google_requests <= GOOGLE_FREE_MONTHLY_CAP:
                raise ValueError(
                    f"--max-google-requests must be between 1 and {GOOGLE_FREE_MONTHLY_CAP}"
                )
            if not 1 <= args.google_timeout_seconds <= 120:
                raise ValueError("--google-timeout-seconds must be between 1 and 120")
            api_key = os.environ.get(args.google_api_key_env)
            if not api_key:
                raise ValueError(f"Environment variable {args.google_api_key_env} is required")
        elif args.max_google_requests and "--max-google-requests" in sys.argv:
            raise ValueError("--max-google-requests requires --google-address-validation")
        else:
            api_key = None
        records = records_from(args.input)
        normalized, exceptions = normalize(records)
        requests = 0
        if api_key:
            google_exceptions, requests = apply_google_validation(
                normalized,
                api_key,
                args.max_google_requests,
                args.google_timeout_seconds,
                args.google_usps_cass,
            )
            exceptions.extend(google_exceptions)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        sys.exit(f"Address normalization failed: {exc}")
    # Rule 9: a control that processed nothing has not passed. Given 18 documents
    # this normalized 0 addresses, sent 0 validation requests, raised 0
    # exceptions and exited 0 -- indistinguishable from a corpus whose addresses
    # were all clean. entity_resolve.py already reports its own empty run this
    # way; this one reported success over no work. Whether the layout carries no
    # address or extraction missed every one is exactly the question a reader
    # cannot answer from a silent zero, so it is asked rather than assumed.
    normalized_count = sum(len(item["address_normalizations"]) for item in normalized)
    findings = []
    if normalized and not normalized_count:
        findings.append(
            f"No address was extracted from {len(normalized)} documents, so nothing was "
            "normalized or validated. Retain this as review work and verify whether the "
            "source layout carries no address or extraction missed them."
        )
        exceptions.append(
            {
                "priority": "normal",
                "document_id": "",
                "page_id": "",
                "region_id": "",
                "field": "address",
                "reason": findings[0],
                "review_source": "address_normalize",
                "disposition": "client_review_required",
            }
        )
    summary = {
        "generated_at": datetime.now(UTC).isoformat(),
        "documents": len(normalized),
        "addresses_normalized": normalized_count,
        "client_review_items": len(exceptions),
        "findings": findings,
        "external_validation": {
            "provider": "google_maps_address_validation" if api_key else "not_requested",
            "credential_reference": args.google_api_key_env if api_key else None,
            "requests_sent": requests,
            "per_run_limit": args.max_google_requests,
            "google_free_monthly_cap": GOOGLE_FREE_MONTHLY_CAP,
            "usps_cass_enabled": bool(api_key and args.google_usps_cass),
        },
    }
    Path(args.out).write_text(
        json.dumps({"summary": summary, "documents": normalized}, indent=2) + "\n"
    )
    Path(args.exceptions).write_text(
        json.dumps({"summary": {"count": len(exceptions)}, "exceptions": exceptions}, indent=2)
        + "\n"
    )
    if not args.quiet:
        print(f"Normalized addresses: {summary['addresses_normalized']}")
        print(f"Google validation requests: {requests}")
        print(f"Client review items: {len(exceptions)}")
        for finding in findings:
            print(f"  - {finding}")


if __name__ == "__main__":
    main()
