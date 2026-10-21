# arXiv crawler (Milestone 1)

## Setup
```bash
python -m venv .venv && source .venv/bin/activate     # Python 3.10+
pip install -r src/requirements.txt
export CRAWLER_USER_AGENT="FIT-HCMUS-IntroDS-crawler/1.0 (your.name@student.hcmus.edu.vn)"
export S2_API_KEY="..."        # free Semantic Scholar key, strongly recommended
```

## Run one shard (each member runs their own 5000 ids)
```bash
python -m src.tools.split_ids --input master_ids.txt --shards 4 --output-dir ids   # leader, once
python -m src.run_crawl --ids-file ids/shard_1.txt --stage discovery               # first, alone
# then, in two terminals at the same time (different hosts, so both limits are respected):
python -m src.run_crawl --ids-file ids/shard_1.txt --stage download
python -m src.run_crawl --ids-file ids/shard_1.txt --stage references
python -m src.tools.collect_stats --data-dir data                                  # numbers for the report
```
Every stage is resumable: finished versions and papers are skipped, failed ones are retried on rerun.
Use `--limit 20` for a quick test.

## Output layout (not committed)
`data/<2310-12345>/metadata.json, references.json, v<k>/status.json, v<k>/src/*.tex|*.bib`
References come from Semantic Scholar per paper, not per version, so they live at paper level.
