# Chạy Milestone 3 từ đầu đến Hugging Face (không dùng `build_dataset`)

Chạy từ root repo, Python 3.10+. Thư mục output `work_m3/` không commit.

```bash
pip install -r src/requirements-m3.txt
```

Ký hiệu đường dẫn (thay bằng đường dẫn thật trên máy/Drive):

| Biến | Ý nghĩa |
|---|---|
| `M2=.../m2_full_v5/exports/<export-id>/tables` | chứa `elements/ hierarchy/ bibtex/` |
| `M1_*` | `references.parquet` và `papers.parquet` của 4 range (`range_2512`, `range_2602`, `range_2605`, `range_2606`) |
| `SEL=src/tools/m3_selection/selection.csv` | selection v3 đã chốt, **không chạy lại `split.py`** |

## Thứ tự các bước

| # | Bước | Ai/khi nào | Output |
|---|---|---|---|
| 1 | Gán nhãn thủ công + tự động, gộp nhãn (xem `M3_LABELLING.md`) | từng người cho range của mình | `work_m3/labels.csv` |
| 2 | **`build_features.py`**: nối bibtex + references → pairs → features | chạy 1 lần cho cả nhóm (không cần nhãn, có thể chạy ngay) | `work_m3/features/` |
| 3 | `train.py`: baseline, LightGBM ranker, ablation | cần bước 1 và 2 | `work_m3/models/` |
| 4 | `predict.py`: bảng `predictions` | mỗi người một lần với `--owner` | `work_m3/predictions_<mssv>/` |
| 5 | `evaluate.py`: MRR@5 (test thủ công / tự động tách riêng) | sau bước 4 | `work_m3/eval_report.json` |
| 6 | Xuất 5 bảng còn lại theo ID range, viết dataset card, upload | mỗi người cho range của mình | `hf_out_<mssv>/` |

## Bước 2 — nối bibtex + references → pairs → features

```bash
python src/build_features.py \
  --selection $SEL \
  --bibtex $M2/bibtex \
  --references M1_2512/references.parquet M1_2602/references.parquet M1_2605/references.parquet M1_2606/references.parquet \
  --out-dir work_m3
```

Script làm gì:

1. Đọc `selection.csv`, giữ bài có `partition` ∈ {train, valid, test} và `kind` ∈ {manual, auto}. Bài `reserve`/`dropped` bị bỏ.
2. Đọc `bibtex` và `references` bằng `pyarrow` **có lọc theo `arxiv_id` ngay lúc đọc**, theo batch, nên không nạp cả bảng 904k dòng vào RAM.
3. Theo từng nhóm 100 bài (`--chunk-papers`): `pairs.build_pairs` tạo ma trận `m × n` của từng bài (chỉ ghép entry với reference **cùng bài**, ID reference được chuẩn hóa bỏ hậu tố version), rồi `features.build_features` tính đặc trưng. Mỗi nhóm ghi 1 shard `features-NNNNN.parquet`. Vì nhóm luôn gồm bài trọn vẹn nên các đặc trưng tương đối (`*_rank`, `*_gap_best`) đúng.
4. Ghi `work_m3/features_summary.json`: số bài chọn, số bài có pair, tổng pair, bài bị bỏ và lý do (`skipped_no_bibtex`, `skipped_no_references`, `skipped_no_pairs`), SHA-256 của selection.

Tùy chọn hay dùng: `--owner 24127465` (chỉ bài của một người), `--save-pairs` (ghi thêm bảng pair thô để phân tích lỗi, dung lượng lớn), `--drop-future` (bỏ cặp có reference sau năm của bài), `--strict` (thoát mã 1 nếu có bài không ra pair).

Bảng features **không chứa nhãn**; `train.py` tự nối `labels.csv` và `selection.csv`. Sau khi chạy, xem `features_summary.json`: `n_papers_with_pairs` nên bằng `n_selected`. Bài thiếu bibtex/references phải được xử lý (đưa lên nhóm), không tự thay bài.

## Bước 3–5 — huấn luyện, dự đoán, đánh giá

```bash
python src/train.py --features work_m3/features --labels work_m3/labels.csv \
  --selection $SEL --out-dir work_m3/models

# mỗi người một lần, đổi --owner
python src/predict.py --features work_m3/features --selection $SEL \
  --model work_m3/models/lgbm_ranker.txt --feature-cols work_m3/models/feature_cols.json \
  --labels work_m3/labels.csv --owner 24127465 --out-dir work_m3/predictions_24127465

python src/evaluate.py --predictions work_m3/predictions_24127465 --labels work_m3/labels.csv \
  --selection $SEL --out-dir work_m3
```

`predict.py` ghi sharded Parquet đúng schema `predictions` của đề (9 cột, `partition` là dictionary). Đọc `work_m3/models/train_metrics.json` và `ablation_results.csv` cho poster.

## Bước 6 — dựng dataset Hugging Face thủ công

Mỗi người một dataset cho ID range của mình. Cấu trúc:

```
hf_out_<mssv>/
├── README.md              # dataset card (YAML bên dưới)
└── data/
    ├── papers/ references/ elements/ hierarchy/ bibtex/    # lọc theo range
    └── predictions/                                        # từ bước 4
```

**6a. Lọc 5 bảng theo range.** Dùng ID range của mình (ví dụ Ngọc `2602.09870`–`2602.14869`). `arxiv_id` cùng tiền tố YYMM nên so sánh chuỗi là đúng.

```python
# src/export_range.py  -- chạy: python src/export_range.py <lo> <hi> <out_dir> <table>=<path> ...
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
```

```bash
python src/export_range.py 2602.09870 2602.14869 hf_out_24127465 \
  papers=M1_2602/papers.parquet references=M1_2602/references.parquet \
  elements=$M2/elements hierarchy=$M2/hierarchy bibtex=$M2/bibtex
mkdir -p hf_out_24127465/data/predictions
cp work_m3/predictions_24127465/*.parquet hf_out_24127465/data/predictions/
```

Giữ nguyên schema nguồn của M1/M2 (đã có kiểu tường minh). Không đổi sang JSON/nested.

**6b. Kiểm tra trước khi upload** (số dòng khớp nguồn, đủ 5.000 `arxiv_id`, `predictions` chỉ chứa bài train/valid/test của owner):

```python
import pyarrow.dataset as ds
for t in ["papers", "references", "elements", "hierarchy", "bibtex", "predictions"]:
    d = ds.dataset(f"hf_out_24127465/data/{t}", format="parquet")
    ids = d.to_table(columns=["arxiv_id"]).column(0).unique()
    print(t, d.count_rows(), "rows,", len(ids), "papers,", d.schema.names)
```

Đối chiếu tổng 4 range với bản chung: `elements` 9.913.475, `hierarchy` 13.309.173, `bibtex` 904.091.

**6c. Dataset card** `hf_out_24127465/README.md`:

```yaml
---
configs:
- config_name: papers
  data_files: [{split: train, path: "data/papers/*.parquet"}]
- config_name: references
  data_files: [{split: train, path: "data/references/*.parquet"}]
- config_name: elements
  data_files: [{split: train, path: "data/elements/*.parquet"}]
- config_name: hierarchy
  data_files: [{split: train, path: "data/hierarchy/*.parquet"}]
- config_name: bibtex
  data_files: [{split: train, path: "data/bibtex/*.parquet"}]
- config_name: predictions
  data_files: [{split: train, path: "data/predictions/*.parquet"}]
---
```

Phần nội dung card ghi: commit M2 `29a14fd` + `parser_sha256`, seed/lệnh `split.py` và SHA-256 `candidates.csv`, giới hạn dữ liệu (mục 7b trong `HUONG_DAN_NHOM_M3.md`), ghi chú 74 bài có key `...__candidate`.

**6d. Upload và kiểm tra streaming:**

```python
from huggingface_hub import HfApi
api = HfApi()   # đã `huggingface-cli login` hoặc đặt HF_TOKEN
api.create_repo("<user>/<repo>", repo_type="dataset", exist_ok=True)   # public mặc định
api.upload_folder(folder_path="hf_out_24127465", repo_id="<user>/<repo>", repo_type="dataset")
```

```python
from datasets import load_dataset
for t in ["papers", "references", "elements", "hierarchy", "bibtex", "predictions"]:
    print(t, next(iter(load_dataset("<user>/<repo>", t, split="train", streaming=True))))
```

Upload bằng web UI cũng được, nhưng giới hạn số file mỗi lần nên dùng `upload_folder` khi nhiều shard.

## Lỗi thường gặp

- `n_papers_with_pairs < n_selected`: xem `skipped_*` trong `features_summary.json`. Nguyên nhân thường là bài không có reference arXiv.
- `train.py` báo không có dòng nào cho một partition: `labels.csv` chưa phủ bài của partition đó, hoặc `arxiv_id` lệch định dạng (dấu chấm trong bảng, gạch ngang trong tên thư mục).
- Đọc CSV bằng pandas mà không có `dtype=str` sẽ biến `2602.00001` thành số thực. Dùng Parquet hoặc `dtype=str`.
