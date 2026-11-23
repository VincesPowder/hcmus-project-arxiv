# MANUAL LABELING — Milestone 3

## 1. Mục đích

Mỗi annotator gán nhãn thủ công cho 5 publication thuộc `manual` của mình trong `selection.csv`.

- Mỗi publication cần ít nhất 20 cặp BibTeX entry → reference đúng.
- Tổng cộng cần ít nhất 100 cặp được gán nhãn.
- `ref_arxiv_id` là ground truth do người gán nhãn quyết định.
- `best_suggestion` chỉ là gợi ý để hỗ trợ tìm kiếm, KHÔNG phải ground truth.

## 2. Tạo các file CSV


```bash
cd /content/hcmus-project-arxiv

python src/label_sheet.py make   --mssv 24127465   --bibtex /content/drive/MyDrive/arXiv-metadata-hcmus/milestone2_outputs/m2_full_v5/exports/20261120-203957-9b2330/tables/bibtex   --references /content/drive/MyDrive/arXiv-metadata-hcmus/2602.09870-2602.14869/work/tables/references.parquet   --selection /content/drive/MyDrive/arXiv-metadata-hcmus/m3_selection/selection.csv   --out-dir /content/drive/MyDrive/arXiv-metadata-hcmus/labeling/24127465
```

Mỗi publication tạo 2 file:

- `<paper>.csv`: file để gán nhãn.
- `<paper>_references.csv`: danh sách reference của chính publication đó để đối chiếu.

## 3. Cấu trúc file `<paper>.csv`

CSV có các cột:

```text
arxiv_id
citation_key
entry_title
entry_authors
entry_year
entry_venue
best_suggestion
ref_arxiv_id
match_detail
note
```

### Ý nghĩa

- `arxiv_id`: publication đang được gán nhãn.
- `citation_key`: citation key của BibTeX entry.
- `entry_title`: tiêu đề trong BibTeX.
- `entry_authors`: tác giả trong BibTeX.
- `entry_year`: năm của BibTeX entry.
- `entry_venue`: venue/journal/url của entry.
- `best_suggestion`: **gợi ý tốt nhất do thuật toán tính ra**. Chỉ dùng để hỗ trợ tìm reference nhanh hơn.
- `ref_arxiv_id`: **ground truth do người gán nhãn nhập**. Chỉ nhập một arXiv ID đúng hoặc `none`.
- `match_detail`: giải thích vì sao chọn reference đó.
- `note`: ghi chú thêm nếu cần.

`best_suggestion` KHÔNG được tự động sao chép sang `ref_arxiv_id`.

## 4. Cách gán nhãn thủ công

Mở `<paper>.csv` và `<paper>_references.csv` trong Google Sheets.

Với từng BibTeX entry:

1. Đọc `entry_title`.
2. So sánh với `ref_title` trong `<paper>_references.csv`.
3. Kiểm tra `entry_authors` và `ref_authors`, ưu tiên họ của tác giả đầu tiên.
4. Kiểm tra `entry_year` và `ref_submission_date`.
5. Kiểm tra thêm arXiv ID, DOI, venue nếu có.
6. Có thể xem `best_suggestion` trước để biết reference nào nên kiểm tra đầu tiên.
7. **Tự xác nhận lại bằng thông tin của reference**, không mặc nhiên chấp nhận suggestion.

### `ref_arxiv_id`

- Nếu tìm thấy đúng reference: nhập **một** arXiv ID.
- Nếu đã kiểm tra toàn bộ reference của publication và không có tác phẩm tương ứng: nhập `none`.
- Nếu chưa làm hoặc chưa chắc chắn: để trống.
- Không nhập nhiều ID vào một ô.

Ví dụ:

```text
best_suggestion: 2511.19116
ref_arxiv_id:    2511.19116
match_detail:    Title + first author + year matched
```

Hoặc:

```text
best_suggestion: 2505.13632
ref_arxiv_id:    none
match_detail:    No matching reference found
```

## 5. `best_suggestion` là gì?

`best_suggestion` là **một reference duy nhất có điểm cao nhất** theo bộ chấm điểm hiện tại.

Nó được ghi trực tiếp vào CSV để bạn có thể nhìn ngay trong Google Sheets.

Điều quan trọng:

- Đây chỉ là candidate/hint.
- Không dùng suggestion làm ground truth.
- Không tự động điền `ref_arxiv_id`.
- Vẫn phải đối chiếu title, authors, year, venue/arXiv ID.
- Nếu suggestion sai, hãy chọn reference đúng trong `<paper>_references.csv`.

Nếu muốn không in suggestion ra terminal khi chạy `make`, có thể dùng:

```bash
--no-suggestions
```

Tùy chọn này chỉ ẩn dòng suggestion trên terminal; cột `best_suggestion` trong CSV vẫn được tạo.

## 6. Lưu ý về arXiv ID trong Google Sheets

Nên đặt các cột chứa arXiv ID thành **Plain text** trước khi nhập/chỉnh sửa.

Mục đích là tránh Google Sheets biến:

```text
2511.19116
```

thành dạng số có dấu phân cách như:

```text
251.119.116
```

Script `collect` có xử lý một số dạng format bị Google Sheets làm thay đổi, nhưng tốt nhất vẫn nên đặt cột ID thành Plain text ngay từ đầu.

## 7. Sau khi gán nhãn

Download từng sheet bằng:

**File → Download → Comma-separated values (.csv)**

Đặt các CSV đã điền vào một thư mục riêng, ví dụ:

```text
/content/drive/MyDrive/arXiv-metadata-hcmus/labeling/24127465_filled
```

Chỉ đưa các file:

```text
2602.11395.csv
2602.12886.csv
2602.11055.csv
2602.10677.csv
2602.11244.csv
```

Không đưa các file:

```text
*_references.csv
```

## 8. Collect và kiểm tra

Sau khi hoàn tất 5 publication:

```bash
cd /content/hcmus-project-arxiv

python src/label_sheet.py collect   --mssv 24127465   --sheets-dir /content/drive/MyDrive/arXiv-metadata-hcmus/labeling/24127465_filled   --references /content/drive/MyDrive/arXiv-metadata-hcmus/2602.09870-2602.14869/work/tables/references.parquet   --selection /content/drive/MyDrive/arXiv-metadata-hcmus/m3_selection/selection.csv   --out labels/labels_manual_24127465.csv
```

Script sẽ kiểm tra:

- citation key bị trùng;
- `ref_arxiv_id` có thực sự thuộc references của cùng publication hay không;
- publication nào chưa đạt 20 matched pairs;
- tổng số matched pairs có đạt 100 hay chưa;
- các dòng đã gán nhãn nhưng thiếu `match_detail`.

`best_suggestion` không được dùng làm ground truth khi collect.

## 9. Nguyên tắc quan trọng

```text
best_suggestion = gợi ý của thuật toán
ref_arxiv_id    = quyết định thủ công / ground truth
match_detail    = bằng chứng giải thích quyết định
```

Không được biến:

```text
best_suggestion → ref_arxiv_id
```

mà không kiểm tra thủ công.
