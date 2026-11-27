#!/usr/bin/env python3
"""split.py - chọn publication thủ công / tự động và chia tập train/valid/test cho cả nhóm (Milestone 3).

CHẠY MỘT LẦN cho cả nhóm, rồi chia sẻ selection.csv. Mỗi người chỉ lọc các dòng có owner của mình.

    python split.py --candidates candidates.csv --out-dir m3_selection
    python split.py --out-dir m3_selection --replace <bài_thủ_công_hỏng> <bài_dự_phòng>

Quy tắc (theo đề M3):
  * Mỗi người: 5 bài thủ công (+4 dự phòng) trong ID range của mình, ~500 bài tự động (10%), không trùng nhau.
  * Test = 1 thủ công + 1 tự động / người, Valid = 1 + 1 / người (gộp nhóm: 4+4 và 4+4), Train = phần còn lại.
Độ khó bài thủ công: ratio = n_any_hit / n_refs (tỷ lệ reference mà regex ID / tiêu đề trùng hệt bắt được).
  ratio thấp = nhiều reference regex KHÔNG bắt được = ca khó thật sự. Test thủ công lấy từ tầng khó.
Tính tái lập: xếp hạng bằng SHA-256 của "seed:mục đích:arxiv_id", không phụ thuộc phiên bản pandas/numpy.
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

import pandas as pd

OWNERS = ["24127192", "24127262", "24127465", "24127534"]
# (ô, tầng): thap = khó, vua = vừa, cao = dễ. Test lấy tầng khó; valid lấy tầng vừa.
SLOTS = [("test", "thap"), ("valid", "vua"), ("train1", "cao"), ("train2", "vua"), ("train3", "thap")]
RESERVE_TIERS = ["thap", "thap", "vua", "cao"]       # bài khó dễ không đủ 20 cặp nên dự phòng 2 bài khó
FALLBACK = {"cao": ["cao", "vua", "thap"], "vua": ["vua", "cao", "thap"], "thap": ["thap", "vua", "cao"]}
EASY_RATIO = 0.85                                    # ratio >= 0.85: dễ; hard_ratio <= ratio < 0.85: vừa
MIN_BIB, MIN_REFS, MIN_PAIRS_MANUAL = 20, 20, 20
AUTO_MIN_HITS = 3
AUTO_EVAL_MIN_HITS = 20     # bài tự động vào test/valid cần đủ nhiều cặp để MRR@5 có nghĩa
KIND_ORDER = {"manual": 0, "manual_reserve": 1, "manual_dropped": 2, "auto": 3}


def rank(seed, purpose, arxiv_id):
    return hashlib.sha256(f"{seed}:{purpose}:{arxiv_id}".encode()).hexdigest()


def tier_of(ratio, hard_ratio):
    return "thap" if ratio < hard_ratio else ("vua" if ratio < EASY_RATIO else "cao")


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def load_candidates(path):
    c = pd.read_csv(path, dtype={"arxiv_id": str, "owner": str}, keep_default_na=True)
    need = {"arxiv_id", "owner", "n_bib", "n_refs", "n_any_hit", "bad_version", "conflict", "auto_ok"}
    missing = need - set(c.columns)
    if missing:
        sys.exit(f"candidates.csv thiếu cột: {sorted(missing)}")
    c["bad_version"] = c["bad_version"].fillna("").astype(str)
    for col in ("conflict", "auto_ok"):
        c[col] = c[col].astype(str).str.lower().eq("true")
    c["ratio"] = (c.n_any_hit / c.n_refs.clip(lower=1)).round(3)
    return c


def manual_pool(g, a):
    """Pool thủ công = bài sạch, cỡ vừa sức gán nhãn, và (đủ cặp ước lượng HOẶC là ca khó đủ điều kiện)."""
    clean = (g.bad_version == "") & (~g.conflict)
    base = g[clean & g.n_bib.between(MIN_BIB, a.manual_max_bib) & g.n_refs.between(MIN_REFS, a.manual_max_refs)
             & (g.n_any_hit <= g.n_refs)]
    normal = base.n_any_hit >= MIN_PAIRS_MANUAL
    # Ca khó: regex bắt được ít, nhưng còn nhiều reference chưa bắt (nhiều khả năng vẫn có entry tương ứng)
    # và vài entry đã khớp chứng tỏ bibtex của bài parse được (không phải bài hỏng bibtex).
    hard = ((base.ratio < a.hard_ratio) & (base.n_refs >= a.hard_min_refs)
            & (base.n_bib >= a.hard_min_bib) & (base.n_any_hit >= a.hard_min_hits))
    pool = base[normal | hard].copy()
    pool["tier"] = pool.ratio.map(lambda r: tier_of(r, a.hard_ratio))
    pool["_r"] = [rank(a.seed, "manual", x) for x in pool.arxiv_id]
    return pool


def select_manual(g, a, owner, warnings):
    pool = manual_pool(g, a)
    counts = pool.tier.value_counts().to_dict()
    used, rows = set(), []

    def pick(wanted, slot, kind, partition):
        for t in FALLBACK[wanted]:
            ok = pool[(pool.tier == t) & ~pool.arxiv_id.isin(used)].sort_values(["_r", "arxiv_id"])
            if len(ok):
                row = ok.iloc[0]
                used.add(row.arxiv_id)
                if t != wanted:
                    warnings.append(f"{owner}: ô {slot} cần tầng '{wanted}' nhưng hết bài, dùng tầng '{t}'")
                rows.append({"arxiv_id": row.arxiv_id, "owner": owner, "kind": kind, "partition": partition,
                             "slot": slot, "tier": t, "n_bib": row.n_bib, "n_refs": row.n_refs,
                             "n_any_hit": row.n_any_hit, "ratio": row.ratio})
                return
        sys.exit(f"{owner}: pool thủ công không đủ bài cho ô {slot}")

    for slot, tier in SLOTS:
        pick(tier, slot, "manual", slot.rstrip("0123456789"))   # train1.. -> train
    for i, tier in enumerate(RESERVE_TIERS, 1):
        pick(tier, f"reserve{i}_{tier}", "manual_reserve", "reserve")
    return rows, used, counts


def select_auto(g, used, a, owner):
    pool = g[g.auto_ok & ~g.arxiv_id.isin(used) & (g.bad_version == "") & (~g.conflict)].copy()
    pool["tier"] = pool.ratio.map(lambda r: tier_of(r, a.hard_ratio))
    pool["_r"] = [rank(a.seed, "auto", x) for x in pool.arxiv_id]
    pool = pool.sort_values(["_r", "arxiv_id"])
    ev = pool[pool.n_any_hit >= AUTO_EVAL_MIN_HITS].head(2)
    if len(ev) < 2 or len(pool) < a.n_auto:
        sys.exit(f"{owner}: pool tự động không đủ (có {len(pool)}, cần {a.n_auto}, bài eval {len(ev)})")
    rest = pool[~pool.arxiv_id.isin(ev.arxiv_id)].head(a.n_auto - 2)
    rows = []
    for part, df in (("test", ev.iloc[[0]]), ("valid", ev.iloc[[1]]), ("train", rest)):
        for _, row in df.iterrows():
            rows.append({"arxiv_id": row.arxiv_id, "owner": owner, "kind": "auto", "partition": part,
                         "slot": "", "tier": row.tier, "n_bib": row.n_bib, "n_refs": row.n_refs,
                         "n_any_hit": row.n_any_hit, "ratio": row.ratio})
    return rows


def validate(sel, cand, n_auto):
    bad = set(cand[(cand.bad_version != "") | cand.conflict].arxiv_id)
    active = sel[sel.kind.isin(["manual", "auto"])]
    assert sel.arxiv_id.is_unique, "có bài xuất hiện nhiều lần"
    assert not (set(sel.arxiv_id) & bad), "có bài lỗi version/xung đột"
    assert set(sel.owner) == set(OWNERS), "thiếu thành viên"
    for o in OWNERS:
        s = sel[sel.owner == o]
        a = s[s.kind == "auto"]
        m = active[(active.owner == o) & (active.kind == "manual")]
        assert len(m) == 5, f"{o}: {len(m)} bài thủ công (cần 5)"
        assert len(a) == n_auto, f"{o}: {len(a)} bài tự động (cần {n_auto})"
        for kind, part in (("manual", "test"), ("manual", "valid"), ("auto", "test"), ("auto", "valid")):
            n = len(active[(active.owner == o) & (active.kind == kind) & (active.partition == part)])
            assert n == 1, f"{o}: {kind}/{part} có {n} bài (cần 1)"
    for part in ("test", "valid"):
        for kind in ("manual", "auto"):
            n = len(active[(active.partition == part) & (active.kind == kind)])
            assert n == 4, f"nhóm: {kind}/{part} = {n} (cần 4)"


def main():
    p = argparse.ArgumentParser(description="Chọn pub và chia tập M3 (chạy một lần cho cả nhóm)")
    p.add_argument("--candidates", default="candidates.csv")
    p.add_argument("--out-dir", default="m3_selection")
    p.add_argument("--seed", default="m3-2026")
    p.add_argument("--n-auto", type=int, default=500, help="số bài tự động mỗi người (10%% của 5000)")
    p.add_argument("--manual-max-bib", type=int, default=100, help="tối đa entry bibtex / bài thủ công")
    p.add_argument("--manual-max-refs", type=int, default=150, help="tối đa reference arXiv / bài thủ công")
    p.add_argument("--hard-ratio", type=float, default=0.6, help="ratio < giá trị này = tầng khó")
    p.add_argument("--hard-min-refs", type=int, default=30, help="ca khó: tối thiểu reference arXiv")
    p.add_argument("--hard-min-bib", type=int, default=30, help="ca khó: tối thiểu entry bibtex")
    p.add_argument("--hard-min-hits", type=int, default=10, help="ca khó: tối thiểu cặp regex đã bắt được")
    p.add_argument("--replace", nargs=2, metavar=("HONG", "DU_PHONG"),
                   help="thay bài thủ công hỏng bằng bài dự phòng của cùng người")
    a = p.parse_args()
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    sel_path, log_path = out / "selection.csv", out / "selection_log.json"

    if a.replace:
        sel = pd.read_csv(sel_path, dtype={"arxiv_id": str, "owner": str}, keep_default_na=False)
        bad_id, res_id = a.replace
        b, r = sel[sel.arxiv_id == bad_id], sel[sel.arxiv_id == res_id]
        if len(b) != 1 or b.iloc[0].kind != "manual":
            sys.exit(f"{bad_id} không phải bài thủ công đang dùng")
        if len(r) != 1 or r.iloc[0].kind != "manual_reserve":
            sys.exit(f"{res_id} không phải bài dự phòng còn trống")
        if b.iloc[0].owner != r.iloc[0].owner:
            sys.exit("bài hỏng và bài dự phòng phải cùng một người")
        ib, ir = b.index[0], r.index[0]
        entry = {"dropped": bad_id, "promoted": res_id, "owner": b.iloc[0].owner,
                 "inherited_partition": b.iloc[0].partition, "inherited_slot": b.iloc[0].slot,
                 "tier_dropped": b.iloc[0].tier, "tier_promoted": r.iloc[0].tier}
        sel.loc[ir, ["kind", "partition", "slot"]] = ["manual", b.iloc[0].partition, b.iloc[0].slot]
        sel.loc[ib, ["kind", "partition", "slot"]] = ["manual_dropped", "dropped", ""]
        log = json.loads(log_path.read_text(encoding="utf-8")) if log_path.exists() else []
        log.append(entry)
        log_path.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
        sel.to_csv(sel_path, index=False)
        print("Đã thay:", entry)
        if entry["tier_dropped"] != entry["tier_promoted"]:
            print("CẢNH BÁO: khác tầng độ khó, cân nhắc nếu bài này vào test/valid")
        return

    cand = load_candidates(a.candidates)
    warnings, rows, pools = [], [], {}
    for o in OWNERS:
        g = cand[cand.owner == o]
        if g.empty:
            sys.exit(f"không có bài nào của owner {o}")
        mrows, used, counts = select_manual(g, a, o, warnings)
        pools[o] = counts
        rows += mrows + select_auto(g, used, a, o)
    sel = pd.DataFrame(rows)
    validate(sel, cand, a.n_auto)
    sel["_k"] = sel.kind.map(KIND_ORDER)
    sel = sel.sort_values(["owner", "_k", "partition", "arxiv_id"]).drop(columns="_k")
    sel.to_csv(sel_path, index=False)
    meta = {"seed": a.seed, "n_auto_per_owner": a.n_auto, "candidates_file": str(a.candidates),
            "candidates_sha256": sha256_file(a.candidates), "slots": SLOTS, "reserve_tiers": RESERVE_TIERS,
            "hard_ratio": a.hard_ratio, "easy_ratio": EASY_RATIO, "hard_min_refs": a.hard_min_refs,
            "hard_min_bib": a.hard_min_bib, "hard_min_hits": a.hard_min_hits,
            "manual_max_bib": a.manual_max_bib, "manual_max_refs": a.manual_max_refs,
            "auto_eval_min_hits": AUTO_EVAL_MIN_HITS, "manual_pool_sizes": pools,
            "pandas": pd.__version__, "python": sys.version.split()[0], "warnings": warnings}
    (out / "selection_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Đã ghi {sel_path} ({len(sel)} dòng) và selection_meta.json")
    print("Input sha256:", meta["candidates_sha256"])
    print("\nKích thước pool thủ công theo tầng:")
    print(pd.DataFrame(pools).T.reindex(columns=["thap", "vua", "cao"]).fillna(0).astype(int).to_string())
    for w in warnings:
        print("CẢNH BÁO:", w)
    print("\n" + sel.groupby(["owner", "kind", "partition"]).size().unstack(fill_value=0).to_string())
    print("\nBài thủ công chính và dự phòng:")
    print(sel[sel.kind.isin(["manual", "manual_reserve"])]
          [["owner", "kind", "slot", "arxiv_id", "tier", "n_bib", "n_refs", "n_any_hit", "ratio"]]
          .to_string(index=False))


if __name__ == "__main__":
    main()