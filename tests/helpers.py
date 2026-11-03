"""Shared test helpers: build tiny fake M1 corpora in a temp folder."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def write(base, relative, text, encoding="utf-8"):
    path = Path(base) / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode(encoding))
    return path


def make_version(base, files):
    """Write {relative path: text} under base and return base."""
    for relative, text in files.items():
        write(base, relative, text)
    return Path(base)


def make_paper(corpus, key, arxiv_id, versions):
    """versions: {number: None (never downloaded) | "no_tex" | {relpath: text}} -> paper folder."""
    paper_dir = Path(corpus) / key
    write(paper_dir, "paper.json", json.dumps(
        {"arxiv_id": arxiv_id, "versions": [{"arxiv_id": arxiv_id, "version": n} for n in versions]}))
    for number, content in versions.items():
        version_dir = paper_dir / f"v{number}"
        version_dir.mkdir(parents=True, exist_ok=True)
        if content is None:
            continue
        status = "no_tex" if content == "no_tex" else "ok"
        write(version_dir, "metadata.json", json.dumps({"stats": {"status": status}}))
        if isinstance(content, dict):
            for relative, text in content.items():
                write(version_dir, relative, text)
    return paper_dir


DOC = "\\documentclass{article}\n\\begin{document}\n%s\n\\end{document}\n"
