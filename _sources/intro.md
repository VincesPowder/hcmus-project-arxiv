# IntroDS — Nhóm 2

Jupyter Book tổng hợp kết quả **Milestone 1 (Data Crawling)** và **Milestone 2 (Hierarchical Parsing and Standardization)** trên tập arXiv được giao.

## Dữ liệu đầu vào

Corpus được giao gồm **5000 publication** (arXiv ID từ `2512.18231` đến `2512.23230`), mỗi publication có thể có nhiều version. Toàn bộ mã nguồn nằm trong repository
[`VincesPowder/hcmus-project-arxiv`](https://github.com/VincesPowder/hcmus-project-arxiv).

## Tổng quan pipeline hai milestone

```{mermaid}
flowchart LR
    A[arXiv API] -->|discovery| B[Download .tex/.bib<br/>mọi version]
    B --> C[S2 Graph API<br/>reference metadata]
    B --> D[Corpus<br/>5000 papers]
    C --> E[references.parquet]
    D --> F[gather<br/>compilation path]
    F --> G[hier_parser<br/>cây hierarchy]
    F --> H[refs<br/>.bib + \bibitem]
    G --> I[elements & hierarchy]
    H --> J[bibtex]
    I --> K[Bảng chuẩn hoá]
    J --> K
```

- **Milestone 1** — crawl metadata + `.tex`/`.bib` của mọi version, lấy reference từ Semantic Scholar.
- **Milestone 2** — parse LaTeX thành cây phân cấp, chuẩn hoá, tách references, deduplicate xuyên version → ba bảng `elements`, `hierarchy`, `bibtex`.

## Nội dung book

```{tableofcontents}
```
