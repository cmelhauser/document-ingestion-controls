#!/usr/bin/env python3
"""Find where a dealer or brand trades, from Google Places, as a proposal.

The party master knows a dealer by the names its pages print, and most of those
names carry no address. Google Places can say where a business of that name
trades. This lane asks, for dealer and brand names only and within a per-run
cap, and keeps every candidate Google returned. It accepts a candidate only
when it accounts for every word of the party's name, and then only as a
probable public fact for `augment_export.py --external`: a Places result is not
proof of a legal entity, a current trading name, a relationship or a preferred
site, so every one stays a proposal for client review.

It sends a business name and nothing else. Google Address Validation, which
would send the client's addresses, stays off until the client approves it.
"""

import argparse
import json
import os
import re
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from cli_help import apply_shared_help
from entity_resolve import is_truncated, normalize_name, similarity
from run_io import empty_output_path
from runtime_config import env_bool, env_float, env_int, env_value, load_project_env
from schema_discovery import PLACES_ENDPOINT

ARTIFACT_TYPE = "party_public_sources_v1"
# Places is allowed for the names of dealers and brands, never for a customer,
# a person or an address.
LOOKUP_ROLES = frozenset({"dealer", "brand"})
# Fields billed at the Text Search Pro tier; nothing more is asked for.
FIELD_MASK = (
    "places.id,places.displayName,places.formattedAddress,"
    "places.addressComponents,places.businessStatus"
)
# A one-word name is accepted only this close to the candidate's, on the
# resolver's own measure, and only when the word is at least this long: `Wells`
# names too many businesses to identify one.
MATCH_THRESHOLD = 0.8
MIN_SINGLE_WORD = 6
ADC_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
# A name that starts with a number, or pairs a number with one of these words,
# is a place rather than a business: `One Bayfront Suite 540` is where a project
# was delivered, and the lane sends no address.
STREET_WORD = re.compile(
    r"\b(?:suite|ste|blvd|boulevard|avenue|ave|street|road|parkway|pkwy|highway|hwy|floor|drive|lane)\b",
    re.IGNORECASE,
)


def load_json(path, what):
    """A JSON object from disk, refusing anything that is not one."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"not a {what}: {path}")
    return data


# A name that is only a city and a state is a place too: `Miami, FL` found a
# furniture warehouse in Medley.
CITY_STATE = re.compile(r"[A-Za-z .']+,\s*[A-Z]{2}")


def address_like(name):
    """Whether a name reads as a place rather than a business."""
    text = str(name or "").strip()
    return (
        bool(re.match(r"\d", text))
        or bool(re.search(r"\d", text) and STREET_WORD.search(text))
        or bool(CITY_STATE.fullmatch(text))
    )


def cut_off_names(names):
    """Names another name continues mid-word, and lone words several firms begin with.

    `Cornerwi` is `CORNERWISE DESIGN SERVICES` or `CORNERWISE FURNITURE INT.`
    cut off, and `Corporate` begins three firms' names. A lookup of either
    describes whichever business Google ranks first, not the party.
    """
    loose = {name: " ".join(re.sub(r"[^a-z0-9]+", " ", name.lower()).split()) for name in names}
    cut = set()
    for name, short in loose.items():
        if not short:
            continue
        longer = [other for other in loose.values() if other != short and other.startswith(short)]
        if any(other[len(short)] != " " for other in longer):
            cut.add(name)
        elif " " not in short and len({other.split()[1] for other in longer}) > 1:
            cut.add(name)
    return cut


def select(parties, limit, known=()):
    """The dealer and brand parties worth a lookup, most-mentioned first.

    A name the page cut off is not looked up: Google would find a business
    called `Office Surr`, and whatever it said would describe the stump. A name
    that reads as a place is not sent at all, and a name a public source
    already covers costs no request. Returns the parties to ask about, those
    the cap leaves unasked, and why others were passed over.
    """
    known = {normalize_name(name) for name in known}
    cut = cut_off_names([p.get("canonical_name") or "" for p in parties])
    chosen, passed = [], Counter()
    for party in sorted(
        parties, key=lambda p: (-p.get("mention_count", 0), p.get("party_key", ""))
    ):
        name = party.get("canonical_name") or ""
        if not LOOKUP_ROLES & set(party.get("roles") or []):
            continue
        if address_like(name):
            passed["looks_like_an_address"] += 1
        elif is_truncated(name) or name in cut or len(normalize_name(name)) < 4:
            passed["name_cut_off_or_too_short"] += 1
        elif normalize_name(name) in known:
            passed["already_covered_by_a_public_source"] += 1
        else:
            chosen.append(party)
    return chosen[:limit], chosen[limit:], passed


def default_credentials():
    """Application Default Credentials, refreshed: the sign-in `reauthorize_google.py` keeps."""
    import google.auth
    from google.auth.transport.requests import Request as AuthRequest

    credentials, project = google.auth.default(scopes=[ADC_SCOPE])
    credentials.refresh(AuthRequest())
    return credentials, project


def headers(auth, key_env, credentials=default_credentials):
    """Request headers for the chosen credential, which is never written anywhere.

    `key` reads the restricted Places key from the variable `key_env` names;
    `adc` uses Application Default Credentials, billed to their quota project.
    """
    base = {"Content-Type": "application/json", "X-Goog-FieldMask": FIELD_MASK}
    if auth == "key":
        key = os.environ.get(key_env)
        if not key:
            raise RuntimeError(f"places_key_missing: {key_env} is not set")
        return {**base, "X-Goog-Api-Key": key}
    creds, project = credentials()
    quota = getattr(creds, "quota_project_id", None) or project
    if not getattr(creds, "token", None) or not quota:
        raise RuntimeError("places_adc_unavailable: no token or quota project")
    return {**base, "Authorization": f"Bearer {creds.token}", "X-Goog-User-Project": quota}


def search(query, request_headers, timeout, opener=urlopen):
    """The places Google returns for a text query; nothing is chosen here."""
    request = Request(  # noqa: S310 - a fixed https Places endpoint, never a caller's URL
        PLACES_ENDPOINT,
        data=json.dumps({"textQuery": query, "pageSize": 5}).encode(),
        method="POST",
        headers=request_headers,
    )
    try:
        with opener(request, timeout=timeout) as response:
            data = json.loads(response.read().decode())
    except (HTTPError, URLError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("places_request_failed") from exc
    # Places answers a query that matched nothing with an empty object.
    places = data.get("places", []) if isinstance(data, dict) else None
    if not isinstance(places, list):
        raise RuntimeError("places_response_invalid")
    return places


def component(place, kind, short=False):
    """One part of a candidate's address, by its Google type."""
    for part in place.get("addressComponents") or []:
        if kind in (part.get("types") or []):
            return part.get("shortText" if short else "longText") or ""
    return ""


def summarize(place):
    """What a candidate says about where the business is."""
    return {
        "place_id": place.get("id", ""),
        "name": (place.get("displayName") or {}).get("text", ""),
        "address": place.get("formattedAddress", ""),
        "city": component(place, "locality"),
        "state": component(place, "administrative_area_level_1", short=True),
        "country": component(place, "country", short=True),
        "business_status": place.get("businessStatus", ""),
    }


def accounts_for(name, candidate):
    """Whether a candidate accounts for every word of the party's name.

    The first word -- the business, not where it trades -- must be in the
    candidate's name: `Alhambra Plaza` is a project, and a furniture shop at
    4217 Alhambra plaza Blvd prints both its words only in its address. Every
    other word must be in the candidate's name, begin one of its words -- the
    page may have cut the name off -- or name the place it trades. A word left
    over names something else: `BGE Contract Furniture de Puer` is the Puerto
    Rico firm, and the New York one sharing the rest of its name does not
    account for `Puer`. A one-letter word must be a whole word of the
    candidate's name, since every name has a word beginning with `e` and
    `INTERIOR E` is a name the page cut off.
    """
    theirs = normalize_name(candidate["name"]).split()
    where = normalize_name(
        " ".join((candidate["address"], candidate["city"], candidate["state"]))
    ).split()

    def named(word):
        if len(word) == 1:
            return word in theirs
        return any(other.startswith(word) for other in theirs)

    words = normalize_name(name).split()
    return (
        bool(words)
        and named(words[0])
        and all(named(word) or (len(word) > 1 and word in where) for word in words[1:])
    )


def acceptable(name, candidate, countries=()):
    """Whether a candidate is the party, as far as a name can say.

    It must account for every word of the party's name. That is what makes
    `KD Frost` the `KD Frost Architectural Interior Supply` its similarity
    scores at 0.38, and keeps the New York BGE off the Puerto Rico one it
    scores at 0.81. A lone word needs more: at least six letters and a close
    match, or `Kingfisher` becomes a restaurant. A name that reads as a place is
    never accepted on either side -- `Alhambra Plaza` is a project on a
    boulevard, and `2020 Alhambra plaza` another building on it -- and neither
    is a candidate outside the named countries.
    """
    if address_like(name) or address_like(candidate["name"]):
        return False
    if countries and candidate["country"] not in countries:
        return False
    words = normalize_name(name).split()
    if len(words) == 1 and len(words[0]) < MIN_SINGLE_WORD:
        return False
    score = similarity(normalize_name(name), normalize_name(candidate["name"]))
    if score == 1.0:
        return True
    if not accounts_for(name, candidate):
        return False
    return len(words) > 1 or score >= MATCH_THRESHOLD


def best(name, candidates, countries=()):
    """The best acceptable candidate and its similarity; Google's order breaks ties.

    When none is acceptable the nearest similarity is still returned, so an
    operator can see how close the nearest miss came.
    """
    scored = [
        (similarity(normalize_name(name), normalize_name(c["name"])), -i)
        for i, c in enumerate(candidates)
    ]
    if not scored:
        return None, 0.0
    fits = [pair for pair in scored if acceptable(name, candidates[-pair[1]], countries)]
    score, rank = max(fits or scored)
    return (candidates[-rank] if fits else None), score


def proposal(name, variants, match, score):
    """The public-source entry an accepted candidate becomes."""
    return {
        "names": sorted({name, *(variants or [])}),
        "identity": match["name"],
        "city": match["city"],
        "state": match["state"],
        "country": match["country"],
        "address": match["address"],
        "website": "",
        "business_status": match["business_status"],
        "source": f"google_places:{match['place_id']}",
        "confidence": "probable",
        "match_score": round(score, 4),
    }


def look_up(parties, request_headers, timeout, context="", opener=urlopen, countries=()):
    """Ask once about each party; keep every answer and accept only close ones."""
    accepted, lookups, failures = [], [], Counter()
    for party in parties:
        name = party["canonical_name"]
        query = " ".join(part for part in (name, context) if part)
        entry = {"party_key": party.get("party_key", ""), "name": name, "query": query}
        try:
            raw = search(query, request_headers, timeout, opener)
        except RuntimeError as exc:
            failures[str(exc)] += 1
            lookups.append({**entry, "error": str(exc)})
            continue
        candidates = [summarize(place) for place in raw]
        match, score = best(name, candidates, countries)
        lookups.append(
            {
                **entry,
                "score": round(score, 4),
                "accepted": match is not None,
                "candidates": candidates,
                "raw": raw,
            }
        )
        if match:
            accepted.append(proposal(name, party.get("name_variants"), match, score))
    return accepted, lookups, failures


def rescore(lookups, parties, countries=()):
    """Decide again from the candidates a previous run kept, sending nothing.

    The acceptance rule can be refined after the requests are paid for, since
    every candidate Google returned is retained; re-deciding costs no request.
    """
    variants = {p.get("party_key"): p.get("name_variants") for p in parties}
    accepted, redone, failures = [], [], Counter()
    for entry in lookups:
        if "error" in entry:
            failures[entry["error"]] += 1
            redone.append(entry)
            continue
        match, score = best(entry["name"], entry["candidates"], countries)
        redone.append({**entry, "score": round(score, 4), "accepted": match is not None})
        if match:
            accepted.append(proposal(entry["name"], variants.get(entry["party_key"]), match, score))
    return accepted, redone, failures


def build_report(
    accepted, lookups, failures, unsent, passed, auth, limit, countries=(), rescored_from=None
):
    """Every candidate asked for, the few accepted, and what was not asked and why."""
    unmatched = sum(1 for entry in lookups if entry.get("accepted") is False)
    if rescored_from:
        asked = (
            f"Decided again from the candidates retained in {rescored_from}; no request was sent."
        )
    elif unsent:
        asked = (
            f"{len(unsent)} parties were not asked about because the limit of {limit} was reached."
        )
    else:
        asked = "Every selected party was asked about within the per-run limit."
    report = {
        "artifact_type": ARTIFACT_TYPE,
        "retrieved": datetime.now(UTC).date().isoformat(),
        "provider": "google_places",
        "auth": auth,
        "countries": sorted(countries),
        "note": (
            "Candidate locations from Google Places for dealer and brand names only. "
            "`confidence` is `probable` because a Places match rests on a name; each "
            "is a proposal for client review and never overwrites a reading."
        ),
        "client_review_required": True,
        "parties": accepted,
        "lookups": lookups,
        "summary": {
            "requests_sent": 0 if rescored_from else len(lookups),
            "per_run_limit": limit,
            "parties_accepted": len(accepted),
            "parties_without_a_close_match": unmatched,
            "requests_failed": sum(failures.values()),
            "failures": dict(failures),
            "not_asked_over_the_limit": len(unsent),
            "passed_over": dict(passed),
        },
        "findings": [
            f"{len(accepted)} of {len(lookups)} parties matched a Places candidate that "
            "accounts for every word of their name; every candidate is kept under `lookups`.",
            asked,
        ],
    }
    if rescored_from:
        report["rescored_from"] = rescored_from
    return report


def build_parser():
    """The command line."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("parties", help="Party master from entity_resolve.py.")
    parser.add_argument(
        "--out", required=True, help="Candidate and accepted locations (a new file)."
    )
    parser.add_argument(
        "--enabled",
        action=argparse.BooleanOptionalAction,
        default=env_bool("GOOGLE_PLACES_ENABLED", False),
        help="Allow Places requests for this run. Off unless enabled here or in the environment.",
    )
    parser.add_argument(
        "--auth",
        choices=("key", "adc"),
        default=env_value("GOOGLE_PLACES_AUTH", "key"),
        help="Credential: the restricted Places key, or Application Default Credentials.",
    )
    parser.add_argument(
        "--api-key-env",
        default=env_value("GOOGLE_PLACES_API_KEY_ENV", "GOOGLE_PLACES_API_KEY"),
        help="Name of the environment variable holding the Places key, when --auth is key.",
    )
    parser.add_argument(
        "--max-requests",
        type=int,
        default=env_int("GOOGLE_PLACES_MAX_REQUESTS", 100),
        help="Most Places requests this run may make; one request per party, no retries.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=env_float("GOOGLE_PLACES_TIMEOUT_SECONDS", 15.0),
        help="Per-request timeout, in seconds.",
    )
    parser.add_argument(
        "--context", default="", help="Words added to every query, such as the trade."
    )
    parser.add_argument(
        "--skip-known",
        help="A party_public_sources_v1 file; parties whose names it covers are not looked up.",
    )
    parser.add_argument(
        "--countries",
        default="",
        help="Comma-separated ISO 3166-1 alpha-2 codes a party may trade in; a candidate "
        "elsewhere is kept, never accepted.",
    )
    parser.add_argument(
        "--from-lookups",
        help="A previous run's output; decide again from its retained candidates, sending nothing.",
    )
    parser.add_argument("--quiet", action="store_true", help="Suppress the printed summary.")
    apply_shared_help(parser)
    return parser


def main(argv=None, opener=urlopen, credentials=default_credentials):
    """Look up the dealer and brand parties, and write what Google said."""
    load_project_env()
    args = build_parser().parse_args(argv)
    if not args.from_lookups and not args.enabled:
        sys.exit("Google Places is disabled; set GOOGLE_PLACES_ENABLED=true or pass --enabled.")
    if args.max_requests < 1 or args.timeout_seconds <= 0:
        sys.exit("--max-requests must be positive and --timeout-seconds above zero.")
    countries = frozenset(
        code.strip().upper() for code in args.countries.split(",") if code.strip()
    )
    try:
        empty_output_path(args.out)
        parties = load_json(args.parties, "party master").get("parties") or []
        if args.from_lookups:
            prior = load_json(args.from_lookups, "party locations artifact")
            accepted, lookups, failures = rescore(prior.get("lookups") or [], parties, countries)
            report = build_report(
                accepted,
                lookups,
                failures,
                [],
                {},
                prior.get("auth", ""),
                args.max_requests,
                countries,
                rescored_from=args.from_lookups,
            )
        else:
            known = []
            if args.skip_known:
                sources = load_json(args.skip_known, "public-source file")
                known = [n for p in sources.get("parties") or [] for n in p.get("names") or []]
            chosen, unsent, passed = select(parties, args.max_requests, known)
            request_headers = headers(args.auth, args.api_key_env, credentials)
            accepted, lookups, failures = look_up(
                chosen, request_headers, args.timeout_seconds, args.context, opener, countries
            )
            report = build_report(
                accepted, lookups, failures, unsent, passed, args.auth, args.max_requests, countries
            )
        Path(args.out).write_text(json.dumps(report, indent=2), encoding="utf-8")
    except (OSError, ValueError, RuntimeError) as exc:
        sys.exit(f"Party location lookup failed: {exc}")
    if not args.quiet:
        summary = report["summary"]
        print(f"requests sent: {summary['requests_sent']} (limit {summary['per_run_limit']})")
        print(f"  accepted: {summary['parties_accepted']}")
        print(f"  no close match: {summary['parties_without_a_close_match']}")
        print(f"  failed: {summary['requests_failed']}")
        print(f"  not asked, over the limit: {summary['not_asked_over_the_limit']}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
