"""Typed, snapshot-bound multidimensional analytics over approved CRM facts."""

from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation

from canonical_load import LOAD_ORDER, schema_catalog, stable_checksum
from client_platform_config import fingerprint
from crm_service import _connect, _metadata, _records, _scalar

AGGREGATES = {"sum", "count", "count_distinct", "average", "minimum", "maximum"}
DATE_GRAINS = {"day", "month", "quarter", "year", "fiscal_quarter", "fiscal_year"}
MAX_DIMENSIONS = 8
MAX_METRICS = 12


def _object(value, label, allowed, required=()):
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    if set(value) - set(allowed):
        raise ValueError(
            f"{label} contains unsupported fields: {sorted(set(value) - set(allowed))}"
        )
    if set(required) - set(value):
        raise ValueError(f"{label} is missing fields: {sorted(set(required) - set(value))}")
    return value


def _name(value, label):
    if not isinstance(value, str) or not value or len(value) > 100:
        raise ValueError(f"{label} must be non-empty text of at most 100 characters")
    return value


def _list(value, label, maximum):
    if not isinstance(value, list) or not value or len(value) > maximum:
        raise ValueError(f"{label} must be a non-empty list of at most {maximum} values")
    if not all(isinstance(item, str) and item for item in value) or len(set(value)) != len(value):
        raise ValueError(f"{label} values must be unique non-empty strings")
    return value


def validate_semantic_model(settings):
    """Validate datasets against the canonical catalog before reading any rows."""
    catalog = schema_catalog()
    datasets = settings["datasets"]
    for dataset_name, dataset in datasets.items():
        _name(dataset_name, "dataset name")
        _object(
            dataset,
            f"dataset {dataset_name}",
            {"base_table", "joins", "dimensions", "metrics"},
            {"base_table", "joins", "dimensions", "metrics"},
        )
        base = dataset["base_table"]
        if base not in LOAD_ORDER:
            raise ValueError(f"dataset {dataset_name} has unsupported base_table")
        available = set(catalog[base])
        joins = dataset["joins"]
        if not isinstance(joins, list) or len(joins) > 12:
            raise ValueError(f"dataset {dataset_name} joins must be a list of at most 12 items")
        aliases = set()
        for index, join in enumerate(joins):
            label = f"dataset {dataset_name} join {index}"
            _object(
                join,
                label,
                {"table", "local_field", "foreign_field", "fields"},
                {"table", "local_field", "foreign_field", "fields"},
            )
            table = join["table"]
            if table not in LOAD_ORDER:
                raise ValueError(f"{label} has unsupported table")
            if join["local_field"] not in available or join["foreign_field"] not in catalog[table]:
                raise ValueError(f"{label} references an unsupported join field")
            if not isinstance(join["fields"], dict) or not join["fields"]:
                raise ValueError(f"{label}.fields must be a non-empty object")
            for alias, field in join["fields"].items():
                _name(alias, f"{label} alias")
                if alias in available or alias in aliases or field not in catalog[table]:
                    raise ValueError(f"{label} contains an invalid or duplicate projected field")
                aliases.add(alias)
        available |= aliases
        dimensions = dataset["dimensions"]
        metrics = dataset["metrics"]
        if not isinstance(dimensions, dict) or not dimensions or len(dimensions) > 100:
            raise ValueError(f"dataset {dataset_name} dimensions must be a non-empty object")
        if not isinstance(metrics, dict) or not metrics or len(metrics) > 100:
            raise ValueError(f"dataset {dataset_name} metrics must be a non-empty object")
        for name, dimension in dimensions.items():
            _name(name, "dimension name")
            _object(dimension, f"dimension {name}", {"field", "type"}, {"field", "type"})
            if dimension["field"] not in available or dimension["type"] not in {
                "string",
                "date",
                "number",
            }:
                raise ValueError(f"dimension {name} has an unsupported field or type")
        for name, metric in metrics.items():
            _name(name, "metric name")
            _object(
                metric,
                f"metric {name}",
                {"field", "aggregation", "currency_dimension"},
                {"field", "aggregation"},
            )
            if metric["field"] not in available or metric["aggregation"] not in AGGREGATES:
                raise ValueError(f"metric {name} has an unsupported field or aggregation")
            currency = metric.get("currency_dimension")
            if currency is not None and currency not in dimensions:
                raise ValueError(f"metric {name} has an unsupported currency_dimension")
    reports = settings["saved_reports"]
    for name, plan in reports.items():
        _name(name, "saved report name")
        validate_plan(settings, plan)
    return settings


def validate_plan(settings, plan):
    """Validate an analytics plan against the configured semantic model before it runs."""
    _object(
        plan,
        "analytics plan",
        {
            "dataset",
            "dimensions",
            "metrics",
            "filters",
            "having",
            "date_grains",
            "sort",
            "limit",
            "offset",
            "include_totals",
        },
        {"dataset", "metrics"},
    )
    dataset_name = plan["dataset"]
    if dataset_name not in settings["datasets"]:
        raise ValueError("analytics plan names an unsupported dataset")
    dataset = settings["datasets"][dataset_name]
    dimensions = plan.get("dimensions", [])
    if dimensions:
        _list(dimensions, "dimensions", MAX_DIMENSIONS)
    metrics = _list(plan["metrics"], "metrics", MAX_METRICS)
    unknown = set(dimensions) - set(dataset["dimensions"])
    unknown |= set(metrics) - set(dataset["metrics"])
    if unknown:
        raise ValueError(f"analytics plan names unsupported fields: {sorted(unknown)}")
    filters = plan.get("filters", {})
    if (
        not isinstance(filters, dict)
        or len(filters) > 20
        or set(filters) - set(dataset["dimensions"])
    ):
        raise ValueError("filters must name at most 20 declared dimensions")
    having = plan.get("having", {})
    if not isinstance(having, dict) or len(having) > 20 or set(having) - set(metrics):
        raise ValueError("having must name at most 20 selected metrics")
    grains = plan.get("date_grains", {})
    if not isinstance(grains, dict) or set(grains) - set(dimensions):
        raise ValueError("date_grains must name selected dimensions")
    for name, grain in grains.items():
        if dataset["dimensions"][name]["type"] != "date" or grain not in DATE_GRAINS:
            raise ValueError("date_grains require a date dimension and supported grain")
    sort = plan.get("sort", [])
    if sort:
        _list(sort, "sort", 5)
        if {item.removeprefix("-") for item in sort} - (set(dimensions) | set(metrics)):
            raise ValueError("sort must name selected dimensions or metrics")
    limit = plan.get("limit", settings["max_result_rows"])
    offset = plan.get("offset", 0)
    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or not 1 <= limit <= settings["max_result_rows"]
    ):
        raise ValueError("limit is outside the configured result budget")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("offset must be a non-negative integer")
    if not isinstance(plan.get("include_totals", False), bool):
        raise ValueError("include_totals must be Boolean")
    return plan


def semantic_model(config):
    """Return the governed semantic model a client may query, bound to the configuration fingerprint."""
    settings = validate_semantic_model(config["analytics"])
    return {
        "schema_version": "governed_analytics_semantic_model_v1",
        "configuration_sha256": fingerprint(config),
        "timezone": config["client"]["timezone"],
        "fiscal_year_start_month": config["client"]["fiscal_year_start_month"],
        "datasets": settings["datasets"],
        "saved_reports": sorted(settings["saved_reports"]),
        "query_language": {"aggregates": sorted(AGGREGATES), "date_grains": sorted(DATE_GRAINS)},
        "arbitrary_sql_permitted": False,
    }


def _date_bucket(value, grain, fiscal_start):
    try:
        parsed = date.fromisoformat(str(_scalar(value)))
    except (TypeError, ValueError) as exc:
        raise ValueError("date dimension contains a non-ISO value") from exc
    if grain == "day":
        return parsed.isoformat()
    if grain == "month":
        return f"{parsed.year:04d}-{parsed.month:02d}"
    if grain == "quarter":
        return f"{parsed.year:04d}-Q{((parsed.month - 1) // 3) + 1}"
    if grain == "year":
        return str(parsed.year)
    fiscal_year = parsed.year + (fiscal_start != 1 and parsed.month >= fiscal_start)
    if grain == "fiscal_year":
        return f"FY{fiscal_year:04d}"
    fiscal_month = (parsed.month - fiscal_start) % 12
    return f"FY{fiscal_year:04d}-Q{(fiscal_month // 3) + 1}"


def _typed(value, kind):
    value = _scalar(value)
    if value is None:
        return None
    if kind == "number":
        try:
            result = Decimal(str(value))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError("number dimension contains a non-numeric value") from exc
        if not result.is_finite():
            raise ValueError("number dimension contains a non-finite value")
        return result
    if kind == "date":
        try:
            return date.fromisoformat(str(value))
        except ValueError as exc:
            raise ValueError("date dimension contains a non-ISO value") from exc
    return str(value)


def _matches(value, condition, kind="string"):
    condition = (
        {"operator": "eq", "value": condition} if not isinstance(condition, dict) else condition
    )
    _object(condition, "filter", {"operator", "value"}, {"operator", "value"})
    operator, expected = condition["operator"], condition["value"]
    actual = _typed(value, kind)
    if operator == "is_null":
        return actual is None and expected is True
    if operator == "not_null":
        return actual is not None and expected is True
    if actual is None:
        return operator == "eq" and expected is None
    convert = lambda item: _typed(item, kind)  # noqa: E731 - compact typed comparison.
    if operator == "eq":
        return actual == convert(expected)
    if operator == "ne":
        return actual != convert(expected)
    if operator == "in" and isinstance(expected, list) and len(expected) <= 100:
        return actual in {convert(item) for item in expected}
    if operator == "not_in" and isinstance(expected, list) and len(expected) <= 100:
        return actual not in {convert(item) for item in expected}
    if (
        operator in {"contains", "starts_with", "ends_with"}
        and kind == "string"
        and not isinstance(expected, (dict, list))
    ):
        candidate, needle = str(actual).casefold(), str(expected).casefold()
        return {
            "contains": needle in candidate,
            "starts_with": candidate.startswith(needle),
            "ends_with": candidate.endswith(needle),
        }[operator]
    if operator in {"gt", "gte", "lt", "lte"} and not isinstance(expected, (dict, list)):
        target = convert(expected)
        return {
            "gt": actual > target,
            "gte": actual >= target,
            "lt": actual < target,
            "lte": actual <= target,
        }[operator]
    if operator == "between" and isinstance(expected, list) and len(expected) == 2:
        return convert(expected[0]) <= actual <= convert(expected[1])
    raise ValueError("filter uses an unsupported operator or value")


def _sort_value(value, metric=False):
    if value is None:
        return (1, "")
    if metric:
        try:
            return (0, Decimal(str(value)))
        except (InvalidOperation, ValueError):
            return (1, "")
    return (0, str(value))


def _joined_rows(connection, dataset, maximum):
    rows = [dict(row) for row in _records(connection, dataset["base_table"])]
    if len(rows) > maximum:
        raise ValueError("dataset exceeds the configured input-row budget")
    for join in dataset["joins"]:
        foreign_rows = _records(connection, join["table"])
        if len(foreign_rows) > maximum:
            raise ValueError("analytics join exceeds the configured input-row budget")
        index = {}
        for foreign in foreign_rows:
            key = str(_scalar(foreign.get(join["foreign_field"])))
            if key in index:
                raise ValueError("analytics join is not many-to-one; fan-out is refused")
            index[key] = foreign
        for row in rows:
            match = index.get(str(_scalar(row.get(join["local_field"]))))
            for alias, field in join["fields"].items():
                row[alias] = None if match is None else match.get(field)
    return rows


def _aggregate(values, kind):
    scalar = [_scalar(value) for value in values]
    if kind == "count":
        return str(len(scalar)), []
    if kind == "count_distinct":
        return str(len({str(value) for value in scalar if value not in (None, "")})), []
    numbers, rejected = [], []
    for index, value in enumerate(scalar):
        try:
            number = Decimal(str(value))
            if not number.is_finite():
                raise InvalidOperation
            numbers.append(number)
        except (InvalidOperation, ValueError):
            rejected.append(index)
    if rejected or not numbers:
        return None, rejected or list(range(len(scalar)))
    if kind == "sum":
        result = sum(numbers, Decimal("0"))
    elif kind == "average":
        result = sum(numbers, Decimal("0")) / len(numbers)
    elif kind == "minimum":
        result = min(numbers)
    else:
        result = max(numbers)
    return format(result, "f"), []


def query(database, config, plan):
    """Execute one allowlisted plan and account for every selected source row."""
    settings = validate_semantic_model(config["analytics"])
    validate_plan(settings, plan)
    dataset = settings["datasets"][plan["dataset"]]
    dimensions = list(plan.get("dimensions", []))
    metrics = list(plan["metrics"])
    for metric in metrics:
        currency = dataset["metrics"][metric].get("currency_dimension")
        if currency and currency not in dimensions:
            dimensions.append(currency)
    with _connect(database) as connection:
        metadata = _metadata(connection)
        source_rows = _joined_rows(connection, dataset, settings["max_input_rows"])
    selected = []
    for row in source_rows:
        if all(
            _matches(
                row.get(dataset["dimensions"][name]["field"]),
                condition,
                dataset["dimensions"][name]["type"],
            )
            for name, condition in plan.get("filters", {}).items()
        ):
            selected.append(row)
    grouped = defaultdict(list)
    fiscal_start = config["client"]["fiscal_year_start_month"]
    for row in selected:
        key = []
        for name in dimensions:
            value = _scalar(row.get(dataset["dimensions"][name]["field"]))
            grain = plan.get("date_grains", {}).get(name)
            key.append(
                _date_bucket(value, grain, fiscal_start)
                if grain and value not in (None, "")
                else value
            )
        grouped[tuple(key)].append(row)
    results, exceptions = [], []
    for key, rows in grouped.items():
        result = dict(zip(dimensions, key, strict=True))
        for metric in metrics:
            definition = dataset["metrics"][metric]
            value, rejected = _aggregate(
                [row.get(definition["field"]) for row in rows], definition["aggregation"]
            )
            result[metric] = value
            if rejected:
                exceptions.append(
                    {
                        "group": dict(zip(dimensions, key, strict=True)),
                        "metric": metric,
                        "reason": "invalid_or_missing_metric_values",
                        "rejected_rows": len(rejected),
                    }
                )
        result["source_rows"] = len(rows)
        results.append(result)
    grouped_rows = sum(item["source_rows"] for item in results)
    if plan.get("having"):
        results = [
            row
            for row in results
            if all(
                _matches(row.get(metric), condition, "number")
                for metric, condition in plan["having"].items()
            )
        ]
    totals = None
    if plan.get("include_totals"):
        partitions = sorted(
            {
                dataset["metrics"][metric]["currency_dimension"]
                for metric in metrics
                if dataset["metrics"][metric].get("currency_dimension")
            }
        )
        total_groups = defaultdict(list)
        for row in selected:
            key = tuple(
                _scalar(row.get(dataset["dimensions"][name]["field"])) for name in partitions
            )
            total_groups[key].append(row)
        if not total_groups:
            total_groups[tuple(None for _name in partitions)] = []
        items = []
        for key, rows in total_groups.items():
            item = {**dict(zip(partitions, key, strict=True)), "source_rows": len(rows)}
            for metric in metrics:
                definition = dataset["metrics"][metric]
                item[metric], rejected = _aggregate(
                    [row.get(definition["field"]) for row in rows], definition["aggregation"]
                )
                if rejected:
                    exceptions.append(
                        {
                            "group": {**dict(zip(partitions, key, strict=True)), "total": True},
                            "metric": metric,
                            "reason": "invalid_or_missing_metric_values",
                            "rejected_rows": len(rejected),
                        }
                    )
            items.append(item)
        totals = {"partition_dimensions": partitions, "rows": items}
    for field in reversed(plan.get("sort", [])):
        descending = field.startswith("-")
        name = field.removeprefix("-")
        results.sort(
            key=lambda row: _sort_value(row.get(name), name in metrics), reverse=descending
        )
    total = len(results)
    offset, limit = plan.get("offset", 0), plan.get("limit", settings["max_result_rows"])
    page = results[offset : offset + limit]
    payload = {
        "schema_version": "governed_analytics_result_v1",
        "configuration_sha256": fingerprint(config),
        "snapshot": {
            "batch_id": metadata["batch_id"],
            "source_export_sha256": metadata["source_export_sha256"],
        },
        "plan": plan,
        "effective_dimensions": dimensions,
        "dimension_definitions": {name: dataset["dimensions"][name] for name in dimensions},
        "metric_definitions": {name: dataset["metrics"][name] for name in metrics},
        "coverage": {
            "input_rows": len(source_rows),
            "selected_rows": len(selected),
            "grouped_rows": grouped_rows,
            "groups_after_having": len(results),
            "exception_groups": len(exceptions),
        },
        "status": "complete" if not exceptions else "completed_with_exceptions",
        "exceptions": exceptions,
        "total_groups": total,
        "offset": offset,
        "limit": limit,
        "next_offset": offset + len(page) if offset + len(page) < total else None,
        "rows": page,
        "totals": totals,
        "arbitrary_sql_permitted": False,
    }
    payload["result_sha256"] = stable_checksum(payload)
    return payload


def run_saved(database, config, report):
    """Run one configured saved report and bind its result to a checksum."""
    reports = config["analytics"]["saved_reports"]
    if report not in reports:
        raise ValueError("Unknown saved report")
    result = query(database, config, reports[report])
    result["saved_report"] = report
    result["result_sha256"] = stable_checksum(
        {key: value for key, value in result.items() if key != "result_sha256"}
    )
    return result
