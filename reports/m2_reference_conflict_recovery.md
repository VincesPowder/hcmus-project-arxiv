# M2: bảo toàn output khi citation key mâu thuẫn

## Nguyên nhân và thay đổi

Lỗi `citation key '...' identifies conflicting references` xuất hiện khi cùng
một key trong cùng version dẫn tới nhiều reference không vượt qua kiểm tra
DOI/title/year/authors. Trước đây `unify_bibtex` ném exception, khiến cả publication
không có JSON, kể cả cây nội dung đã parse thành công.

Pipeline hiện gọi `unify_bibtex(..., versioned=True, conflict_policy="preserve")`:

- Giữ các reference ứng viên với canonical key riêng, không gộp metadata mâu thuẫn.
- Không để key mơ hồ trùng canonical key của bất kỳ ứng viên nào. Citation gốc
  vẫn chưa được resolve; không chọn tùy tiện reference đầu tiên.
- Tạo alias theo version cho các citation xác định được, kể cả khi canonical key
  phải đổi vì xung đột ở version khác. Key sinh tự động không chiếm key thật của source.
- Giữ `source_keys`, bổ sung `ambiguous_source_keys` ở từng reference và
  `reference_conflicts` ở JSON publication, gồm version, key gốc và `candidate_keys`.
- Tiếp tục xuất cây và ba bảng Parquet. Runner báo `partial` nếu còn key mơ hồ,
  kể cả khi không còn publication bị exception.

API mặc định vẫn dùng policy `raise` cho caller cần kiểm tra nghiêm ngặt.
Policy `preserve` yêu cầu alias theo version. Resume tái sử dụng JSON có xung đột
nếu source/parser không đổi; parse lại source y hệt không giải quyết sự mơ hồ.

## Kiểm chứng 18 publication thực tế

Ngày 2026-11-20, chạy lại đúng 18 publication được báo lỗi từ corpus cục bộ
`work/raw`; không crawl và không chạy lại toàn bộ 5.000/20.000 bài.

Parser SHA-256:
`3b3abaed0f15d9d80b3d09390e8ff21dfabd79b4e4dfec1db96f509b479aada0`.

| Chỉ số | Kết quả |
| --- | ---: |
| Publication có JSON | 18 / 18 |
| Version parsed | 25 / 25 |
| Lỗi cấu trúc hierarchy | 0 |
| Nodes trước dedup, không tính roots | 12.466 |
| Elements sau dedup, không tính roots | 9.121 |
| Parquet elements, gồm roots | 9.146 |
| Parquet hierarchy | 12.466 |
| References trước / sau union | 1.153 / 751 |
| Parquet bibtex | 751 |
| `(publication, version, key)` mơ hồ cần review | 100 |
| Unresolved `(publication, version, key)` xuất hiện trong cây | 98 |

Ba dataset vượt qua schema/count checks. Kiểm tra thêm: canonical keys duy nhất,
cây hợp lệ, mọi `candidate_keys` tồn tại, key mơ hồ không có alias đến một ứng viên
và không trùng canonical key. 134 regression tests vượt qua, gồm chạy toàn bộ code
cells của notebook trên fixture, xuất Parquet và resume với trạng thái `partial`.

| Publication | Số cặp `(version, key)` mơ hồ |
| --- | ---: |
| 2606.01068 | 1 |
| 2606.01093 | 1 |
| 2606.01180 | 1 |
| 2606.01867 | 1 |
| 2606.02653 | 3 |
| 2606.03207 | 25 |
| 2606.03277 | 1 |
| 2606.03445 | 2 |
| 2606.03697 | 2 |
| 2606.03932 | 4 |
| 2606.04100 | 1 |
| 2606.04330 | 24 |
| 2606.04482 | 2 |
| 2606.04540 | 1 |
| 2606.04809 | 1 |
| 2606.04879 | 2 |
| 2606.04951 | 1 |
| 2606.05612 | 27 |

18 exception cũ chỉ cho biết xung đột đầu tiên của mỗi publication. Khi tiếp tục
xử lý mới thấy các key khác và version khác, nên tổng số cặp cần review lớn hơn 18.
Một số key trong bibliography không xuất hiện trong cây nội dung, nên unresolved
citations trong cây ít hơn số cặp mơ hồ.

Đây là khôi phục output và bảo toàn chứng cứ, chưa phải sửa chính xác mọi reference.
Ví dụ `2606.01068 / 22` chứa hai title/authors/year khác nhau thật; trong một số
bài optics, title đọc từ bibitem lại là venue, khác title đầy đủ ở bản ghi khác.
`2606.04809 / P` chỉ khác cách viết dấu gạch nối. Không thể dùng một quy tắc gộp
theo key cho tất cả những trường hợp này mà vẫn giữ độ chính xác.

## Sử dụng trên Colab

1. Mở lại notebook từ branch `m3-data-split`, chạy cell cấu hình và clone/update
   để runtime nhận code mới. Không chỉ chạy lại cell parse trong clone cũ.
2. Giữ `CORPUS_DIRS` là các corpus thực có. Với range 2606 đã xác định:
   `['/content/drive/MyDrive/2606.00734-2606.05733/corpus']`.
3. Dùng `RUN_DIR` mới, ví dụ
   `/content/drive/MyDrive/IntroDS/milestone2_outputs/m2_full_2606_v2`.
   Fingerprint parser đã đổi; không trộn JSON cũ và mới trong cùng run.
4. Chạy notebook. Xem `audit/reference_conflicts.jsonl`,
   `audit/unresolved_citations.csv`, `audit/reference_review.jsonl` và
   `parsed/<paper>.json`. Báo cáo xung đột cũng nằm trong `reports.zip`.
5. Đối chiếu ứng viên với source để sửa key trùng hoặc metadata bị nhận diện sai;
   chỉ hợp nhất khi có đủ chứng cứ. Sau khi sửa source, dùng một run mới.

Artifacts kiểm chứng cục bộ được lưu tại
`output/m2_conflicts_preserved_20261120/{parsed,audit,export,validation.json,tests.log}`
ngoài repository. Không commit raw corpus hoặc các output này vào Git.
