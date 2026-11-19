"""Export existing parsed JSON into fresh Parquet shards and verify schema/counts."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline import build_logs_and_tables, write_json_atomic


def export(parsed, destination, shard_size=250):
    import pyarrow as pa
    import pyarrow.dataset as ds
    parsed, destination = Path(parsed), Path(destination)
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("Use a fresh export directory to avoid stale Parquet shards")
    expected = {"elements": 0, "hierarchy": 0, "bibtex": 0}
    for path in sorted(parsed.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data["stats"]["hierarchy_problems"]:
            raise ValueError(f"Invalid hierarchy: {data['arxiv_id']}")
        for table in expected:
            expected[table] += len(data[table])
    if not any(expected.values()):
        raise ValueError("No parsed records to export")
    papers = build_logs_and_tables(parsed, destination / "logs", destination / "tables", shard_size)
    counts = {}
    for table, n_rows in expected.items():
        if not n_rows:
            counts[table] = 0
            continue
        dataset = ds.dataset(destination / "tables" / table, format="parquet")
        if dataset.count_rows() != n_rows:
            raise ValueError(f"Row count mismatch in {table}")
        identifiers = ["arxiv_id", "element_id" if table != "bibtex" else "citation_key"]
        if table == "hierarchy":
            identifiers.append("parent_id")
        for name in identifiers:
            if dataset.schema.field(name).nullable:
                raise ValueError(f"Non-null schema constraint lost: {table}.{name}")
            if dataset.schema.field(name).type != pa.string():
                raise ValueError(f"Identifier must be string: {table}.{name}")
        if table == "hierarchy" and dataset.schema.field("version").type != pa.int32():
            raise ValueError("version must be int32")
        if table in {"elements", "bibtex"}:
            field = "kind" if table == "elements" else "entry_type"
            if not pa.types.is_dictionary(dataset.schema.field(field).type):
                raise ValueError(f"{field} must be dictionary encoded")
        if table == "bibtex":
            if dataset.schema.field("authors").type != pa.list_(pa.string()):
                raise ValueError("authors must be list<string>")
            if dataset.schema.field("year").type != pa.int32():
                raise ValueError("year must be int32")
        for batch in dataset.to_batches(batch_size=8192):
            for field, column in zip(batch.schema, batch.columns):
                if not field.nullable and column.null_count:
                    raise ValueError(f"Null identifier in {table}.{field.name}")
        counts[table] = n_rows
    result = {"papers_exported": papers, "table_rows": counts,
              "parquet_bytes": sum(p.stat().st_size for p in destination.rglob("*.parquet")),
              "schema_and_count_checks": "passed",
              "provenance_note": "Field conflicts and citation aliases remain in original per-paper JSON."}
    write_json_atomic(destination / "export_validation.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--parsed-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--shard-size", type=int, default=250)
    args = parser.parse_args()
    print(json.dumps(export(args.parsed_dir, args.output_dir, args.shard_size), indent=2))


if __name__ == "__main__":
    main()
