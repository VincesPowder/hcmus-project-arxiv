# Milestone 2 — kiểm chứng parser, BibTeX và tiến độ dữ liệu

Ngày kiểm chứng: 2026-11-19. Phạm vi thao tác: Documents/IntroDS. Đây là báo cáo kết quả thực tế trên dữ liệu đang có; không phải xác nhận hoàn tất 20.000 bài hoặc benchmark Colab.

## 1. Kết luận tiến độ

| Thành viên | Range được giao | Input hiện có | Có output M2 | Chưa có output |
|---|---|---:|---:|---:|
| 24127192 — Dương Huỳnh Minh Khôi | 2606.00734–2606.05733 | 5.000 | 4.982 | 18 (quarantine) |
| 24127262 — Đỗ Thành Vinh | 2605.02338–2605.07337 | Chưa tìm thấy | 0 | 5.000 |
| 24127465 — Tạ Mai Như Ngọc | 2602.09870–2602.14869 | Chưa tìm thấy | 0 | 5.000 |
| 24127534 — Trương Quang Tạo | 2512.18231–2512.23230 | Chưa tìm thấy | 0 | 5.000 |

Đã thử xử lý đủ 5.000 publication có metadata trong work/raw. Không crawl lại. Ba range còn lại không được ngoại suy. Trạng thái toàn nhóm: 4.982/20.000 publication có output, còn thiếu 15.018 output; trong đó 18 có lỗi reference cần review và 15.000 chưa có input trong workspace.

## 2. Parser và các thay đổi đã kiểm chứng

- Heading stack nhận part/chapter/section/subsection/paragraph và dạng starred, kể cả bài không có chapter. Giữ Appendix/Acknowledgements; loại bibliography khỏi hierarchy.
- List/item phân cấp; figure/table là lá figure; display math là lá formula; inline math nằm trong sentence và chuẩn hóa sang dollar delimiters.
- Sửa regex inline math chưa đóng gây backtracking kéo dài trên source thật.
- Sửa tách journal có định dạng ra khỏi title; hỗ trợ title quote trộn kiểu, title chứa năm, tên có dấu TeX và spacing sau et al.
- Mở rộng BibTeX @string chỉ ở bare tokens, không thay từ nằm trong title literal. Xử lý quote có escape và malformed entries.
- Chế độ cited chọn key trước khi parse fields, tránh parse toàn bộ thư viện dùng chung 38–44 MB. Chế độ all/nocite vẫn giữ mọi entry.
- Với cùng key được render trong bibitem và có entry .bib được khai báo: ưu tiên metadata có cấu trúc, lưu bất đồng vào extraction_conflicts.
- Field union bổ sung authors từ các version; mở rộng initials khi tên tương thích; điền field thiếu và lưu giá trị xung đột trong field_conflicts.
- DOI chuẩn hóa là bằng chứng matching mạnh nhất. Title/key chỉ được dùng khi không mâu thuẫn DOI, year hoặc author evidence.
- Citation aliases theo version, hỗ trợ cite/citep/citet/parencite/autocite/textcite. Key được tái sử dụng cho công trình khác giữa version không còn đổi nhầm toàn publication.
- Key trong cùng version trỏ tới reference không tương thích bị quarantine, không tự chọn một reference.
- Pipeline đọc trực tiếp layout paper_metadata.json + sources/vN/manifest.json bên cạnh layout corpus chuẩn. Input không bị sửa.

## 3. Kiểm chứng

**126/126 unittest đạt** trong môi trường Python 3.13 với pandas/pyarrow/psutil. Có test thực dùng gather/parser/refs/pipeline; test plumbing dùng dev_stubs được phân biệt riêng.

Ca hồi quy lấy từ source thực gồm journal trong emph, tên Schütz, author-year/et al., quote trộn kiểu, thư viện BibTeX lớn, macro chứa backslash, key tái sử dụng, DOI xung đột và author lists bổ sung. Test Parquet kiểm tra schema, non-null IDs, dictionary types, list<string> authors, int32 year/version và số dòng khớp JSON.

Mẫu đều 100 bài có 135 version: sau tối ưu đạt 128 parsed + 7 empty, không timeout và không lỗi cây trong lượt mẫu. Lượt toàn range dưới đây được tạo mới sau các sửa cuối, có fingerprint riêng để phân biệt với các lượt thử trước đó.

Pass test, không có lỗi cây và tỷ lệ metadata đầy đủ không tương đương độ chính xác ngữ nghĩa 100%. Chưa có bộ gold annotation độc lập đủ lớn để công bố precision/recall cho toàn corpus.

## 4. Coverage thực tế của range hiện có

| Metric | Giá trị |
|---|---:|
| Publication đã thử xử lý | 5.000 |
| Publication có JSON output | 4.982 |
| Publication có mọi version parsed hoặc empty | 4.932 |
| Publication quarantine vì key reference xung đột | 18 |
| Version được khai báo trong input | 6.785 |
| Version nằm trong các output đã tạo | 6.760 |
| Parsed | 6.392 |
| Empty | 310 |
| Not downloaded | 56 |
| No root | 2 |
| Version thuộc 18 publication quarantine | 25 |
| Timeout / parse exception trong các version có output | 0 / 0 |
| Lỗi quan hệ cây được phát hiện | 0 |

6.392/6.419 source version có manifest extracted được parse (99,58%). Mẫu số gồm cả 25 version thuộc publication quarantine và 2 version no_root. So với tất cả 6.785 version được khai báo, tỷ lệ parsed là 94,21%; empty và not_downloaded vẫn được giữ riêng. Đây là coverage, không phải accuracy của nội dung.

Hai version no_root thuộc 2606.03051: source là plain TeX, không có documentclass/documentstyle hay begin{document}; giữ trạng thái no_root theo quy tắc root của đề thay vì tự đoán.

## 5. Dedup và output

| Metric | Trước dedup | Sau dedup | Giảm |
|---|---:|---:|---:|
| Nodes, không tính document roots | 3.086.577 | 2.381.064 | 22,86% |
| Reference entries | 255.551 | 191.103 | 25,22% |

| Parquet table | Số dòng đã kiểm tra |
|---|---:|
| elements, gồm 6.392 roots | 2.387.456 |
| hierarchy | 3.086.577 |
| bibtex | 191.103 |

Ba bảng đã được xuất thành shard mới và kiểm tra lại số dòng/schema trên dữ liệu thực. Field conflicts, source keys và citation aliases nằm trong JSON gốc và file review; schema Parquet giữ các trường của đề.

## 6. Chất lượng và việc còn phải xử lý

| Cờ cần review | Số lượng |
|---|---:|
| References thiếu title | 6.262 |
| References thiếu authors | 6.728 |
| References thiếu year | 4.965 |
| References thiếu DOI | 125.091 |
| References có field_conflicts | 2.352 |
| References có extraction_conflicts | 62 |
| Bộ (publication, version, key) cite chưa phân giải | 21.400 |

Metadata thiếu có thể đã thiếu trong source; DOI không phải trường bắt buộc. Các cờ trên không được diễn giải thành số lỗi parser. Unresolved citations cần đối chiếu các bib resources thiếu, macro/custom citation commands, source chỉ có bbl và bibliography gốc; không được tự điền reference từ suy đoán.

18 publication bị quarantine được liệt kê trong paper_failures.json. Ví dụ 2606.01068 dùng key 22 cho hai công trình có title/authors/year khác nhau; 2606.04100 dùng Wang2019 cho hai công trình khác nhau. Một số trường hợp là khác biệt trình bày của cùng reference; cần adjudication riêng trước khi quyết định gộp. Không tuyên bố các publication đó hoàn tất.

Giới hạn còn lại: free-form bibitem vẫn là heuristic; journal của bibitem chưa trích xuất tổng quát, entry_type mặc định misc; tên không tuân theo BibTeX author syntax có thể chưa tách hết; macro TeX/conditional includes và thư viện bbl không có trong input còn hạn chế.

## 7. Chi phí kiểm chứng cục bộ

| Metric | Giá trị |
|---|---:|
| Wall time parse 5.000 bài được chọn | 528.896 s |
| Wall time / bài đã thử | 0.1058 s |
| Workers / timeout mỗi version | 4 / 30 s |
| Peak RAM process tree, lấy mẫu 1 giây | 519.38 MiB |
| Average RAM process tree | 216.35 MiB |
| Số mẫu RAM | 522 |
| Output JSON | 628,385,585 bytes |
| Output Parquet | 151,939,648 bytes |

Máy macOS arm64, Python 3.13.13; số đo RAM dùng 1024² bytes. Wall time/RAM ở đây đo giai đoạn parse, chưa gồm tổng hợp audit và export Parquet sau đó. Đây là local verification, chưa thay thế benchmark Google Colab CPU end-to-end theo đề. RSS có thể đếm lặp bộ nhớ chia sẻ và bỏ lỡ peak giữa hai lần lấy mẫu.

## 8. Bằng chứng và tái lập

- output/m2_verified_2606_clean/: parsed JSON, summary.json, source_fingerprint.json, paper_failures.json, coverage.csv, publications.csv, unresolved_citations.csv, reference_review.jsonl.
- output/m2_verified_2606_export/: ba bảng Parquet, logs và export_validation.json.
- output/m2_group_verified/: tổng hợp phạm vi bốn thành viên từ output thực có, giữ ba range còn thiếu.
- Các đường dẫn output tương đối với Documents/IntroDS. Không commit corpus hoặc parsed output vào repository.
- Fingerprint của năm module xử lý: 46f59e8e6a27e611b03b00df7dbaf7a92956f9adb28b5699217ed3df5428b41e.

Chạy tests và hai công cụ audit/export theo README. Công cụ aggregation từ chối trùng publication hoặc trộn parser fingerprint. Cần bổ sung dữ liệu ba range và xử lý danh sách quarantine/unresolved, sau đó mới có thể xác nhận hoàn tất M2 toàn nhóm. Cần benchmark Colab CPU riêng và đưa số liệu cuối vào Jupyter Book; nguồn book hiện đã bị xóa khỏi main ở upstream.

