# M3 – MANUAL LABELING

## 1. Mục tiêu

- Label **5 publications** trong `selection.csv`.
- Mỗi publication ≥ **20 citations**.
- Tổng ≥ **100 labels**.
- `ref_arxiv_id` = **ground truth do mình quyết định**.
- `best_suggestion` = **chỉ là gợi ý**, không được copy tự động.

## 2. Cách chọn reference

Với mỗi citation, kiểm tra:

1. **Title**
2. **Authors** – ưu tiên tác giả đầu tiên
3. **Year**
4. **arXiv ID / DOI / Venue** nếu có

Sau đó đối chiếu với `<paper>_references.csv`.

### Chọn:

## Quy tắc xác định reference đúng

1. **Title**: phải xác định là cùng một publication.
2. **Authors**: dùng để xác nhận, đặc biệt tác giả đầu tiên.
3. **arXiv ID / DOI / Venue**: dùng để xác nhận nếu có.
4. **Year**:
   - `reference year ≤ publication year` → hợp lệ về mặt thời gian.
   - `reference year > publication year` → **không thể là reference đúng**, loại ngay.
   - Year giống hoặc nhỏ hơn chỉ là điều kiện hỗ trợ; vẫn phải kiểm tra title/authors/ID để xác định đúng publication.

### Không được chọn  vì đáp ứng chỉ 1 yêu cầu
- Chỉ cùng chủ đề;
- hoặc chỉ cùng tác giả;
- hoặc chỉ cùng year;
- hoặc `best_suggestion` đề xuất.

### Kết luận

> Chỉ chọn reference khi xác định đó là **cùng một publication** và **reference không xuất hiện sau publication đang xét**.

Nếu đã kiểm tra toàn bộ references mà không có publication phù hợp → chọn `none`.

**Không chọn chỉ vì cùng chủ đề hoặc cùng tác giả.**

## 3. `best_suggestion`

```text
best_suggestion = gợi ý
ref_arxiv_id    = ground truth
```

Luôn kiểm tra lại suggestion trước khi chọn.

## 4. Lưu ý arXiv ID

Đặt cột ID trong Google Sheets thành **Plain text**.

ArXiv có cả ID mới và ID cũ:

```text
1706.03762
cond-mat/0609392
hep-th/9901001
```

**ID kiểu cũ vẫn là arXiv ID hợp lệ → giữ nguyên, không đổi.**

## 5. Sau khi label

Download:

**File → Download → Comma-separated values (.csv)**

Chỉ đưa 5 file publication vào:

```text
2602.11395.csv
2602.12886.csv
2602.11055.csv
2602.10677.csv
2602.11244.csv
```

Không đưa:

```text
*_references.csv
```

## 6. Nguyên tắc quan trọng nhất

```text
best_suggestion → chỉ để tham khảo
ref_arxiv_id    → ground truth
none            → không tìm thấy reference đúng
```

**Không bao giờ copy `best_suggestion` sang `ref_arxiv_id` nếu chưa kiểm tra.**