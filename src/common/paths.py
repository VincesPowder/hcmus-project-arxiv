"""Folder naming and ID list helpers."""
import json
import re
from pathlib import Path

VERSION_SUFFIX_PATTERN = re.compile(r"v\d+$")


def paper_folder_name(arxiv_id: str) -> str:
    """2310.12345 -> 2310-12345 ; hep-th/9901001 -> hep-th_9901001."""
    return arxiv_id.replace("/", "_").replace(".", "-")


def paper_dir(data_dir, arxiv_id: str) -> Path:
    return Path(data_dir) / paper_folder_name(arxiv_id)


def version_dir(data_dir, arxiv_id: str, version: int) -> Path:
    return paper_dir(data_dir, arxiv_id) / f"v{version}"


def normalize_arxiv_id(raw_id: str) -> str:
    """Strip an optional 'arXiv:' prefix and version suffix."""
    cleaned = raw_id.strip()
    if cleaned.lower().startswith("arxiv:"):
        cleaned = cleaned[6:]
    return VERSION_SUFFIX_PATTERN.sub("", cleaned)


def read_ids(ids_file) -> list:
    """Read one arXiv ID per line, skipping blanks and '#' comments, without duplicates."""
    seen, arxiv_ids = set(), []
    for line in Path(ids_file).read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.strip().startswith("#"):
            continue
        arxiv_id = normalize_arxiv_id(line)
        if arxiv_id not in seen:
            seen.add(arxiv_id)
            arxiv_ids.append(arxiv_id)
    return arxiv_ids


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, payload) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    temporary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary_path.replace(path)  # atomic: a killed run never leaves a half-written file
