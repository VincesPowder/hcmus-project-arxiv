"""Split a master ID list into equal contiguous shards, or generate IDs of a monthly range.

  python -m src.tools.split_ids --input master_ids.txt --shards 4 --output-dir ids
  python -m src.tools.split_ids --generate 2310 1 20000 --shards 4 --output-dir ids
"""
import argparse
from pathlib import Path

from ..common.paths import read_ids


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input")
    parser.add_argument("--generate", nargs=3, metavar=("YYMM", "START", "END"))
    parser.add_argument("--shards", type=int, default=4)
    parser.add_argument("--output-dir", default="ids")
    args = parser.parse_args()

    if args.generate:
        prefix, start, end = args.generate[0], int(args.generate[1]), int(args.generate[2])
        arxiv_ids = [f"{prefix}.{number:05d}" for number in range(start, end + 1)]
    else:
        arxiv_ids = read_ids(args.input)
    shard_size = -(-len(arxiv_ids) // args.shards)  # ceiling division
    Path(args.output_dir).mkdir(parents=True, exist_ok=True)
    for index in range(args.shards):
        shard = arxiv_ids[index * shard_size:(index + 1) * shard_size]
        (Path(args.output_dir) / f"shard_{index + 1}.txt").write_text("\n".join(shard) + "\n")
        print(f"shard_{index + 1}.txt: {len(shard)} ids")


if __name__ == "__main__":
    main()
