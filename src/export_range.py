"""Filter an M1/M2 Parquet table by arxiv_id range into sharded Parquet.

Usage: python src/export_range.py <lo> <hi> <out_dir> <table>=<path> ...
"""
import sys
from pathlib import Path
import pyarrow.dataset as ds
import pyarrow.parquet as pq

lo, hi, out = sys.argv[1], sys.argv[2], Path(sys.argv[3])
SHARD_ROWS = 500_000
for spec in sys.argv[4:]:
    table, src = spec.split("=", 1)
    dataset = ds.dataset(src, format="parquet")
    flt = (ds.field("arxiv_id") >= lo) & (ds.field("arxiv_id") <= hi)
    target = out / "data" / table
    target.mkdir(parents=True, exist_ok=True)
    writer, rows, shard, total = None, 0, 0, 0
    for batch in dataset.to_batches(filter=flt, batch_size=65536):
        if writer is None or rows >= SHARD_ROWS:
            if writer:
                writer.close()
            writer = pq.ParquetWriter(target / f"{table}-{shard:05d}.parquet", dataset.schema)
            shard, rows = shard + 1, 0
        writer.write_batch(batch)
        rows += batch.num_rows
        total += batch.num_rows
    if writer:
        writer.close()
    print(f"{table}: {total:,} rows, {shard} shard(s)")
