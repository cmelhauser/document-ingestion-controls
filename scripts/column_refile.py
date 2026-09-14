#!/usr/bin/env python3
"""Re-file a column read into the wrong field, under a named operator authorization.

A printed column the schema gave no home is not skipped: an engine writes it
into whichever field looks closest. Lumen Weft's quote and sales reports print
a Specifier column and no dealer column, and the commission run's export filed
81 of its values -- architects among them -- as the dealer. Two engines agreeing
on that filing says nothing about the field: both read the value right and filed
it wrong, which is the one failure vendor agreement cannot see.

The fix is a mapping amendment, not a repair. The reading moves whole -- value,
candidates, acceptance -- to the field the page's own heading names, and carries
`refiled_from` naming the field it left, the authorization and the evidence, so
the original filing always reads back. The field it left is removed rather than
emptied, because an empty dealer is a claim that the page printed no dealer.

A reading is a settled value or, where the engines did not settle one, the
candidates they read. Only one engine read 22 of the Lumen column's cells, so
consensus left them without a value, and a test on the value alone left all 22
under the dealer -- every one on pages 661 and 663 among them -- to be asked
about as dealers the page never printed.

Three refusals keep this from becoming a way to rewrite readings:

* a rule with no authorization, no evidence, no pages, or the same field on both
  sides is refused before anything is read;
* a reading is never moved onto a field that already holds one: that is two
  readings of one cell, and choosing between them is review, not a mapping;
* a page a rule names that the records do not carry is reported, not skipped,
  and a run that moved nothing at all fails rather than reading as clean.

Nothing here changes a document's review status or clears a control.
"""

import argparse
import json
import re
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from cli_help import apply_shared_help
from consensus import refresh_views
from run_io import empty_output_path

ARTIFACT_TYPE = "column_refile_report_v1"
# The grains a column can be filed under: the header, or one line.
GRAIN = r"(header|lines\[\d+\])"


def load_rules(data):
    """The authorization and its re-files, refusing a rule that cannot be applied."""
    if not isinstance(data, dict):
        raise ValueError("a re-file rule file must be a JSON object")
    authorization = str(data.get("authorization") or "").strip()
    if not authorization:
        raise ValueError("a re-file needs the operator authorization it is applied under")
    refiles = data.get("refiles")
    if not isinstance(refiles, list) or not refiles:
        raise ValueError("the rule file names no re-file")
    rules = []
    for rule in refiles:
        if not isinstance(rule, dict):
            raise ValueError("each re-file must be an object")
        source, target = str(rule.get("from") or ""), str(rule.get("to") or "")
        pages = [str(page) for page in rule.get("pages") or []]
        evidence = str(rule.get("evidence") or "").strip()
        if not source or not target or source == target:
            raise ValueError(f"a re-file needs two different fields: {source!r} to {target!r}")
        if not pages:
            raise ValueError(f"a re-file of {source} names no page")
        if not evidence:
            raise ValueError(f"a re-file of {source} to {target} cites no evidence")
        rules.append({"from": source, "to": target, "pages": pages, "evidence": evidence})
    return authorization, rules


def page_of(document):
    """The page a document is, by the last segment of its id (`...__p0657`)."""
    return str(document.get("document_id", "")).rsplit("__", 1)[-1]


def empty(field):
    """Whether a field object holds no reading: no value, and no candidate either."""
    if not isinstance(field, dict):
        return True
    if field.get("value") not in (None, ""):
        return False
    return all(candidate in (None, "") for candidate in field.get("candidate_values") or [])


def refile(records, authorization, rules):
    """Move every reading each rule names; return what moved, what did not, and why."""
    by_page = {page_of(document): document for document in records.get("documents") or []}
    moved, refused, absent = [], [], []
    for rule in rules:
        pattern = re.compile(rf"^{GRAIN}\.{re.escape(rule['from'])}$")
        for page in rule["pages"]:
            document = by_page.get(page)
            if document is None:
                absent.append(page)
                continue
            fields = document.setdefault("fields", {})
            retired = []
            for path in sorted(fields):
                match = pattern.match(path)
                if not match or empty(fields[path]):
                    continue
                target = f"{match[1]}.{rule['to']}"
                if not empty(fields.get(target)):
                    refused.append(
                        {
                            "document_id": document["document_id"],
                            "field": path,
                            "reason": "the_target_field_holds_a_reading",
                        }
                    )
                    continue
                field = fields.pop(path)
                fields[target] = {
                    **field,
                    "refiled_from": {
                        "field": path,
                        "authorization": authorization,
                        "evidence": rule["evidence"],
                        # The empty field object the reading replaced -- no value
                        # and no candidate -- kept rather than lost.
                        "target_was": fields.get(target),
                    },
                }
                moved.append(
                    {
                        "document_id": document["document_id"],
                        "from": path,
                        "to": target,
                        "value": field.get("value"),
                        "candidate_values": field.get("candidate_values") or [],
                    }
                )
                retired.append(path)
            if retired:
                # The field it left is gone, so its view entry goes too; every
                # other view entry is kept.
                refresh_views(document, retired)
    return moved, refused, absent


def build_report(moved, refused, absent, authorization, rules):
    """What moved, on which pages, and what was left alone and why."""
    return {
        "artifact_type": ARTIFACT_TYPE,
        "generated_at": datetime.now(UTC).isoformat(),
        "authorization": authorization,
        "rules": rules,
        "readings_refiled": len(moved),
        "by_page": dict(sorted(Counter(page_of(entry) for entry in moved).items())),
        "refused": refused,
        "pages_not_in_records": sorted(set(absent)),
        "moved": moved,
        "findings": [
            "Each moved reading keeps everything it carried and names the field it "
            "left, the authorization and the evidence in `refiled_from`.",
            "A reading is never moved onto a field that already holds one.",
            "No document's review status changes and no control is cleared.",
        ],
    }


def build_parser():
    """The command line."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("records", help="Record artifact whose readings are re-filed.")
    parser.add_argument(
        "--rules",
        required=True,
        help="JSON naming the authorization and each re-file: pages, from, to, evidence.",
    )
    parser.add_argument("--out", required=True, help="Re-filed record artifact (a new file).")
    parser.add_argument("--report", required=True, help="What moved, what did not, and why.")
    parser.add_argument("--quiet", action="store_true", help="Suppress the printed summary.")
    apply_shared_help(parser)
    return parser


def main(argv=None):
    """Re-file the readings the rules name, and report what moved."""
    args = build_parser().parse_args(argv)
    try:
        for path in (args.out, args.report):
            empty_output_path(path)
        records = json.loads(Path(args.records).read_text(encoding="utf-8"))
        if not isinstance(records, dict) or not isinstance(records.get("documents"), list):
            raise ValueError(f"not a record artifact: {args.records}")
        rules_file = json.loads(Path(args.rules).read_text(encoding="utf-8"))
        authorization, rules = load_rules(rules_file)
        moved, refused, absent = refile(records, authorization, rules)
        if not moved:
            raise ValueError("the rules moved no reading; a re-file that moved nothing has not run")
        report = build_report(moved, refused, absent, authorization, rules)
        records.setdefault("summary", {})["column_refile"] = {
            "authorization": authorization,
            "readings_refiled": len(moved),
        }
        Path(args.out).write_text(json.dumps(records), encoding="utf-8")
        Path(args.report).write_text(json.dumps(report, indent=2), encoding="utf-8")
    except (OSError, ValueError) as exc:
        sys.exit(f"Column re-file failed: {exc}")
    if not args.quiet:
        print(f"readings re-filed: {report['readings_refiled']}")
        for page, count in report["by_page"].items():
            print(f"  {page}: {count}")
        if refused:
            print(f"  left alone, the target field holds a reading: {len(refused)}")
        if report["pages_not_in_records"]:
            print(f"  pages not in the records: {', '.join(report['pages_not_in_records'])}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
