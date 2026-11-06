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

Các số liệu dưới đây được tổng hợp từ bốn lượt chạy tương ứng với bốn range của nhóm. Wall time và discovery time là tổng thời gian nếu thực hiện bốn lượt chạy nối tiếp.

| Metric | Giá trị |
|---|---|
| Số lượt chạy | 4 |
| Tổng wall time | ~73 153 s (~20 giờ 19 phút) |
| Tổng discovery time | ~2 177 s (~36 phút 17 giây) |
| Tổng request arXiv | 15 789 (throttled: 79) |
| Tổng request Semantic Scholar | 408 (throttled: 207) |
| Tổng thời gian penalty/backoff | arXiv: ~4 694 s; Semantic Scholar: ~6 730 s |
| Peak RAM lớn nhất | ~418 MB |
| Average RAM có trọng số | ~187 MB |

Mỗi lượt crawler chạy tuần tự (`download_workers = 1`) và resume được khi gián đoạn: version/reference đã tải xong được bỏ qua ở lần chạy lại.

## Notebook

```{seealso}
Xem notebook [run_on_colab.ipynb](run_on_colab.ipynb) để chạy lại pipeline crawl.
```
