# Milestone 1 — Data Crawling

Pipeline thu thập metadata, source `.tex`/`.bib` của **mọi version** và reference metadata của **20.000 publication được phân công cho toàn nhóm** (4 thành viên, mỗi thành viên 5.000 publication), đồng thời tôn trọng rate-limit của arXiv (3 s/request) và Semantic Scholar.

## Kết quả chính

| Metric | Giá trị |
|---|---|
| Publications được giao cho toàn nhóm | 20 000 |
| Publications tìm thấy | 20 000 |
| Tổng version directories | 29 085 |
| Version có `.tex` (status `ok`) | 27 492 |
| Version PDF-only / không có `.tex` (status `no_tex`) | 1 593 |
| File `.tex` tải về | 105 841 |
| File `.bib` tải về | 22 313 |
| Số dòng bảng `references` | 372 085 |
| Tổng kích thước corpus | ~6,35 GB |

Các metric trên được tổng hợp từ bốn thư mục `work` tương ứng với bốn range của nhóm, không phải số liệu ngoại suy từ một range 5.000 publication.

## Output tables

```text
corpus/2605-XXXXX/paper.json          # metadata + danh sách version
corpus/2605-XXXXX/references.json     # reference từ Semantic Scholar
corpus/2605-XXXXX/v1/...vN/           # .tex/.bib + metadata.json (mỗi version)
work/tables/papers.parquet            # arxiv_id, title, authors, dates, venue
work/tables/references.parquet        # arxiv_id, ref_arxiv_id, ref_title, ...
```

## Coverage version

```{image} _static/m1_versions.svg
:alt: Milestone 1 version status
:width: 560px
:align: center
```

Trong 29 085 version của 20.000 publication, 94,5 % có source TeX (27 492/29 085) và 5,5 % là PDF-only (1 593/29 085). Version không có TeX **được ghi lại với `status = no_tex`** thay vì bị bỏ qua im lặng — cơ chế này đảm bảo Milestone 2 biết chính xác version nào cần xử lý.

## Hiệu năng

| Metric | Giá trị |
|---|---|
| Wall time (crawl đầy đủ) | ~1 250 s (~21 phút) |
| Discovery time | ~581 s |
| Số request arXiv | 85 (throttled: 0) |
| Peak RAM | ~277 MB |
| Average RAM | ~113 MB |

Crawler chạy tuần tự (`download_workers = 1`), resume được khi gián đoạn: version/reference đã tải xong được bỏ qua ở lần chạy lại.

## Notebook

```{seealso}
Xem notebook [run_on_colab.ipynb](run_on_colab.ipynb) để chạy lại pipeline crawl.
```
