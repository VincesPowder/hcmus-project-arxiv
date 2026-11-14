"""Build report tables/figures from existing M2 output; never parse or crawl."""
import csv
import html
import json
import os
from collections import Counter
from pathlib import Path


def generate(app):
    target = Path(app.srcdir) / "_generated"
    target.mkdir(exist_ok=True)
    paths = [Path(p) for p in os.environ.get("M2_PARSED_DIRS", "").split(os.pathsep) if p]
    metrics_paths = [Path(p) for p in os.environ.get("M2_METRICS_FILES", "").split(os.pathsep) if p]
    counts = Counter()
    totals = Counter()
    papers = []
    seen = set()
    for directory in paths:
        if not directory.is_dir():
            raise ValueError(f"M2 output directory missing: {directory}")
        for path in sorted(directory.glob("*.json")):
            paper = json.loads(path.read_text(encoding="utf-8"))
            key = paper["arxiv_id"]
            if key in seen:
                raise ValueError(f"Duplicate publication in M2 inputs: {key}")
            seen.add(key)
            versions = paper["versions"]
            states = Counter(v["status"] for v in versions)
            counts.update(states)
            for field in ("nodes_before_dedup", "elements_after_dedup", "bib_before_dedup", "bib_after_dedup"):
                totals[field] += paper["stats"][field]
            papers.append({"arxiv_id": key, "versions": len(versions), **{
                s: states[s] for s in ("parsed", "empty", "not_downloaded", "no_root", "timeout", "parse_error")}})
    fields = ["arxiv_id", "versions", "parsed", "empty", "not_downloaded", "no_root", "timeout", "parse_error"]
    with (target / "m2_coverage.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(papers)
    lines = []
    if not papers:
        lines.append("**Chưa có output M2 được cung cấp khi build.** Chưa xác định tỷ lệ parse, coverage, counts trước/sau dedup và benchmark. Không thể dùng thống kê M1 để thay thế.\n")
    else:
        n = sum(counts.values())
        nonempty = n - counts["empty"]
        rate = lambda numerator, denominator: f"{100 * numerator / denominator:.2f}%" if denominator else "Chưa xác định"
        lines += ["| Metric | Giá trị |", "|---|---|",
                  f"| Publication có output | {len(papers):,} / 20,000 |",
                  f"| Chưa có output so với phạm vi được giao | {max(0, 20000-len(papers)):,} |",
                  f"| Version đã quan sát | {n:,} |",
                  f"| Parsed / tổng version quan sát | {counts['parsed']:,} / {n:,} ({rate(counts['parsed'], n)}) |",
                  f"| Parsed / version không empty | {counts['parsed']:,} / {nonempty:,} ({rate(counts['parsed'], nonempty)}) |"]
        lines += [f"| Status `{s}` | {v:,} |" for s, v in sorted(counts.items())]
        lines += [f"| {label} | {totals[field]:,} |" for label, field in (
            ("Nodes trước dedup (không root)", "nodes_before_dedup"),
            ("Elements sau dedup (không root)", "elements_after_dedup"),
            ("References trước dedup", "bib_before_dedup"), ("References sau dedup", "bib_after_dedup"))]
        lines += ["", "{download}`Tải coverage của từng publication <_generated/m2_coverage.csv>`. Chỉ chứa publication có output; các publication vắng mặt được báo trong tổng còn thiếu.", ""]
        # Source SVG is regenerated during each compilation from actual counters.
        maximum = max(counts.values(), default=1)
        width = 720
        svg = ['<svg xmlns="http://www.w3.org/2000/svg" width="720" height="300" viewBox="0 0 720 300">',
               '<title>M2 version status from supplied output</title><rect width="720" height="300" fill="white"/>']
        for i, (status, value) in enumerate(sorted(counts.items())):
            y = 30 + i * 42
            svg += [f'<text x="8" y="{y+16}" font-family="Arial" font-size="14">{html.escape(status)}</text>',
                    f'<rect x="160" y="{y}" width="{450*value/maximum:.1f}" height="24" fill="#2463a0"/>',
                    f'<text x="630" y="{y+16}" font-family="Arial" font-size="14">{value:,}</text>']
        svg.append('</svg>')
        (target / "m2_status.svg").write_text("\n".join(svg), encoding="utf-8")
        lines += ["```{image} _generated/m2_status.svg", ":alt: M2 version statuses from supplied output", "```", ""]
    metrics = [json.loads(p.read_text(encoding="utf-8")) for p in metrics_paths]
    if metrics:
        lines += ["### Số đo hiệu năng được cung cấp", "",
                  "Số đo dưới đây là log được cung cấp, chưa tự xác nhận môi trường Colab CPU hoặc toàn bộ RAM worker. Chỉ gộp các lượt fresh, không chồng lặp, cùng phiên bản mã; không đưa log resume/merge-only vào benchmark tổng.", "",
                  "| Metric | Giá trị |", "|---|---|",
                  f"| Số log benchmark | {len(metrics)} |",
                  f"| Tổng wall time (s) | {sum(m['wall_seconds'] for m in metrics):.3f} |",
                  f"| Peak RAM process chính (MB) | {max(m['max_ram_mb'] for m in metrics):.3f} |",
                  f"| Output JSON (bytes) | {sum(m['output_json_bytes'] for m in metrics):,} |",
                  f"| Output Parquet (bytes) | {sum(m['tables_bytes'] for m in metrics):,} |"]
        processed = sum(m["n_papers_this_run"] - m["status_counts"].get("skipped", 0) for m in metrics)
        if processed:
            lines += [f"| End-to-end wall time / publication thực xử lý (s) | {sum(m['wall_seconds'] for m in metrics)/processed:.4f} |"]
    else:
        lines += ["", "**Chưa cung cấp log benchmark M2.** Wall time, thời gian trung bình, peak RAM và dung lượng output chưa có số đo xác nhận."]
    (target / "m2_statistics.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def setup(app):
    app.connect("builder-inited", generate)
    return {"version": "1.0", "parallel_read_safe": False, "parallel_write_safe": True}
