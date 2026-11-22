# Hướng dẫn nhóm — Milestone 3 (Reference Matching)

Tài liệu này giải thích M3 đang ở đâu, dữ liệu nằm ở đâu, mỗi người làm gì, và các file trao đổi giữa các bước.
Những chỗ ghi **(đề xuất)** là gợi ý của người soạn, nhóm cần thống nhất; những chỗ còn lại lấy từ đề bài, bảng phân công hoặc kết quả đã chạy.

---

## 1. Tình trạng hiện tại

| Hạng mục | Trạng thái |
|---|---|
| M1 (crawl) | Xong, dữ liệu thô nằm trên Drive dùng chung `arXiv-metadata-hcmus/` |
| M2 (parse) | Xong cho cả 20.000 bài (chạy chung một lượt), 0 publication thất bại |
| Chọn bài M3 (`split.py`) | Xong, đã chốt phiên bản **v3** |
| Công cụ gán nhãn, matcher tự động | Chưa biết trạng thái, cần Khôi xác nhận |
| Gán nhãn thủ công | Chưa bắt đầu |
| Đặc trưng, mô hình, `predictions` | Chưa bắt đầu |
| Dataset Hugging Face, poster | Chưa bắt đầu |

### Số liệu M2 cần nhớ (lượt chạy `m2_full_v5`)

- 20.000 publication, 29.085 version: 27.487 `parsed`, 1.593 `empty` (chỉ có PDF), 4 `no_root`, 1 `timeout`.
- Bảng M2: `elements` 9.913.475 dòng, `hierarchy` 13.309.173 dòng, `bibtex` 904.091 dòng.
- Bảng M1 `references` (chỉ giữ reference có arXiv ID): 372.085 dòng, thuộc 17.441 bài. Khoảng 2.559 bài không có reference nào nên không dùng được cho M3.
- 74 bài có citation key mâu thuẫn trong cùng một version: key trong `bibtex` của các bài này là key tổng hợp (dạng `...__candidate`). Các bài này **đã bị loại khỏi mọi pool chọn**.
- Phiên bản parser: commit `29a14fd`, `parser_sha256` bắt đầu `3b3abaed…` (lấy đủ chuỗi trong `parse_metrics.json`). **Không dùng** các bảng M2 cũ ở `arxiv_m2_outputs/range_*/m2_*` (commit `1b7f421`, thiếu 74 bài).

---

## 2. Dữ liệu nằm ở đâu

Các đường dẫn dưới đây là trên **Drive của người đã chạy M2**. Người đó cần chia sẻ thư mục cho cả nhóm (quyền xem) và mỗi người thêm shortcut vào Drive của mình.

| Dữ liệu | Đường dẫn |
|---|---|
| Parquet M2 (3 bảng, gộp 4 range) | `IntroDS/milestone2_outputs/m2_full_v5/exports/20261120-203957-9b2330/tables/{elements,hierarchy,bibtex}/` |
| Kiểm tra chất lượng M2 | `m2_full_v5/audit/` (`coverage.csv`, `reference_conflicts.jsonl`, ...) |
| JSON từng bài (đầy đủ thông tin chẩn đoán) | `m2_full_v5/parsed/<arxiv-id>.json` (tên dùng dấu gạch ngang) |
| Bảng M1 `papers`, `references` theo từng range | `arxiv_m2_outputs/range_<TAG>/m1_tables/` hoặc `arXiv-metadata-hcmus/<range>/work/tables/` |
| Danh sách ứng viên (20.000 bài, kèm cột đếm) | `IntroDS/m3_selection/candidates.csv` |
| **Kết quả chọn bài (dùng chung)** | `m3_selection_v3/selection.csv` và `selection_meta.json` |

Quy tắc dữ liệu:

- Thư mục dùng chung `arXiv-metadata-hcmus/` chỉ được **đọc**, không ghi vào.
- Không đẩy dữ liệu (Parquet, JSON, zip) lên GitHub. Chỉ code và các file nhỏ dạng danh sách ID.
- `arxiv_id` dùng dấu chấm trong bảng (`2602.09870`), nhưng tên thư mục/tên file JSON dùng dấu gạch ngang (`2602-09870`).

### ID range từng người

| MSSV | Họ tên | ID range |
|---|---|---|
| 24127192 | Dương Minh Huỳnh Khôi | `2606.00734` – `2606.05733` |
| 24127262 | Đỗ Thành Vinh | `2605.02338` – `2605.07337` |
| 24127465 | Tạ Mai Như Ngọc | `2602.09870` – `2602.14869` |
| 24127534 | Trương Quang Tạo | `2512.18231` – `2512.23230` |

---

## 3. Yêu cầu của đề (tóm tắt)

- Mỗi người gán nhãn thủ công **5 bài trong ID range của mình**, mỗi bài **≥ 20 cặp** (entry bibtex ↔ reference arXiv) khớp, mỗi người **≥ 100 cặp**.
- Gán nhãn tự động cho **≥ 10%** số bài còn lại (khoảng 500 bài mỗi người), không trùng bài thủ công.
- Chia tập (gộp cả nhóm): **Test** 4 thủ công + 4 tự động, **Valid** 4 + 4, **Train** phần còn lại.
- Không gian ứng viên của mỗi bài: ma trận `m × n` (m = số entry bibtex, n = số reference có arXiv ID của **cùng bài đó**).
- Mô hình trả về top 5 ứng viên (`cand_1` … `cand_5`). Đúng reference nằm ngoài top 5 thì điểm entry đó bằng 0.
- Đánh giá bằng **MRR@5** trên các cặp có nhãn của tập test. Entry không có reference thì không tính.
- Nộp: **mỗi người tự upload một Hugging Face Dataset cho ID range của mình** (sharded Parquet, 6 bảng, có dataset card), dán link công khai vào bài nộp. Code nằm trên 1 repo GitHub chung; poster là 1 file `.pptx` A2 dọc.

Nguồn: các file "Yêu cầu đồ án", "Phân công công việc và quy tắc chọn bộ dataset cho Milestone 3", "Yêu cầu, quy tắc nộp Milestone 3".

---

## 4. Kết quả chọn bài (`split.py`, phiên bản v3)

`split.py` được chạy **một lần cho cả nhóm**. Không ai chạy lại. Nếu chạy lại thì danh sách bài thay đổi và nhãn đã gán mất ý nghĩa.

### Thông số đã chốt (ghi vào báo cáo)

| Mục | Giá trị |
|---|---|
| Lệnh | `python split.py --candidates candidates.csv --out-dir m3_selection_v3 --hard-ratio 0.7 --manual-max-bib 150 --manual-max-refs 250` |
| Seed | `m3-2026` (xếp hạng bằng SHA-256, không phụ thuộc phiên bản thư viện) |
| SHA-256 của `candidates.csv` | `182ace6b68d05ccdef6b340de6c8c820bd59b12636634c2d51c44257dc9d6517` |
| Mỗi người | 5 bài thủ công + 4 dự phòng + 500 bài tự động |

### Cách đọc `selection.csv`

| Cột | Ý nghĩa |
|---|---|
| `arxiv_id`, `owner` | Bài và MSSV chủ range |
| `kind` | `manual` (đang dùng), `manual_reserve` (dự phòng), `manual_dropped` (đã thay), `auto` |
| `partition` | `train`, `valid`, `test`, `reserve` hoặc `dropped` |
| `slot` | Ô vai trò của bài thủ công (`test`, `valid`, `train1`…); bài dự phòng thế chỗ thừa hưởng đúng ô này |
| `tier` | Độ khó: `thap` (khó), `vua` (vừa), `cao` (dễ) |
| `n_bib`, `n_refs`, `n_any_hit`, `ratio` | Số entry bibtex, số reference arXiv, số cặp ước lượng, và `n_any_hit / n_refs` |

Lọc phần của mình:

```python
import pandas as pd
sel = pd.read_csv("selection.csv", dtype=str)
mine = sel[(sel.owner == "24127465") & (sel.kind == "manual")]    # 5 bài thủ công
auto = sel[(sel.owner == "24127465") & (sel.kind == "auto")]      # 500 bài tự động
```

### Độ khó nghĩa là gì

`ratio` là tỷ lệ reference mà regex ID / tiêu đề trùng hệt bắt được. Bài có `ratio` thấp là bài mà nhiều entry **không** chứa arXiv ID, nên mô hình phải dựa vào tác giả, năm, độ tương tự tiêu đề. Test thủ công lấy từ tầng khó (`ratio` 0,54 – 0,68). Đây là mức khó vừa phải, không cực đoan: gần như mọi bài trong tập có `ratio` rất cao, vì reference của Semantic Scholar vốn là những bài có arXiv ID.

### Bài thủ công của từng người

`n_any_hit` là **ước lượng cận dưới** số cặp khớp (chỉ đếm entry có ID hoặc tiêu đề trùng hệt), chưa phải nhãn.

**24127192 (2606)** — tổng ước lượng 131

| Ô | Bài | Tầng | n_bib | n_refs | n_any_hit |
|---|---|---|---|---|---|
| test | `2606.02285` | khó | 73 | 39 | 24 |
| valid | `2606.04955` | vừa | 29 | 24 | 20 |
| train1 | `2606.01753` | dễ | 44 | 25 | 25 |
| train2 | `2606.04575` | vừa | 55 | 51 | 42 |
| train3 | `2606.04623` | khó | 42 | 30 | 20 |
| Dự phòng | `2606.05537` (khó, 20), `2606.02689` (khó, 36), `2606.03024` (vừa, 21), `2606.04295` (dễ, 40) | | | | |

**24127262 (2605)** — tổng ước lượng 193

| Ô | Bài | Tầng | n_bib | n_refs | n_any_hit |
|---|---|---|---|---|---|
| test | `2605.06550` | khó | 64 | 44 | 30 |
| valid | `2605.03025` | vừa | 66 | 65 | 50 |
| train1 | `2605.06342` | dễ | 43 | 35 | 35 |
| train2 | `2605.02890` | vừa | 86 | 66 | 53 |
| train3 | `2605.04281` | khó | 123 | 39 | 25 |
| Dự phòng | `2605.04816` (khó, **19**), `2605.03635` (khó, 28), `2605.02404` (vừa, 25), `2605.06200` (dễ, 23) | | | | |

**24127465 (2602)** — tổng ước lượng 133

| Ô | Bài | Tầng | n_bib | n_refs | n_any_hit |
|---|---|---|---|---|---|
| test | `2602.11395` | khó | 27 | 37 | 20 |
| valid | `2602.12886` | vừa | 37 | 25 | 21 |
| train1 | `2602.11055` | dễ | 63 | 23 | 23 |
| train2 | `2602.10677` | vừa | 37 | 26 | 21 |
| train3 | `2602.11244` | khó | 55 | 77 | 48 |
| Dự phòng | `2602.11694` (khó, **13**), `2602.14763` (khó, 21), `2602.10645` (vừa, 44), `2602.14214` (dễ, 25) | | | | |

**24127534 (2512)** — tổng ước lượng 147

| Ô | Bài | Tầng | n_bib | n_refs | n_any_hit |
|---|---|---|---|---|---|
| test | `2512.19910` | khó | 120 | 31 | **17** |
| valid | `2512.21432` | vừa | 53 | 44 | 37 |
| train1 | `2512.22854` | dễ | 47 | 45 | 45 |
| train2 | `2512.21582` | vừa | 49 | 33 | 28 |
| train3 | `2512.19906` | khó | 62 | 30 | 20 |
| Dự phòng | `2512.21255` (khó, 26), `2512.19916` (khó, 23), `2512.19239` (vừa, 39), `2512.19848` (dễ, 26) | | | | |

### Phân vùng gộp cả nhóm

| | Thủ công | Tự động |
|---|---|---|
| Test | 4 (1 mỗi người, tầng khó) | 4 (1 mỗi người) |
| Valid | 4 (1 mỗi người, tầng vừa) | 4 (1 mỗi người) |
| Train | 12 | 1.992 |

Bài tự động vào test/valid được chọn có `n_any_hit ≥ 20` để MRR@5 có đủ cặp để đo.

---

## 5. Bàn giao theo người

Thứ tự phụ thuộc:

```
selection.csv (xong)
   |
   +--> label_tool / label_auto (Khôi) --> labels.csv --+
   |                                                    |
   +--> pairs / features (Ngọc) ------------------------+--> train / evaluate (Tạo) --> predictions
   |                                                                                        |
   +--> build_dataset / validate (Vinh) ----------------------------------------------------+--> upload HF
```

| Người | Công việc (theo bảng phân công) | Làm được ngay? | Đầu vào | Đầu ra |
|---|---|---|---|---|
| **Khôi** (24127192) | `label_tool.py`: công cụ duyệt từng reference arXiv, gợi ý 5–10 entry gần nhất, cảnh báo một entry gán hai reference | **Có**, và đây là đường găng vì mọi người phải chờ công cụ này để gán nhãn thủ công | `selection.csv`, `bibtex`, `references` | công cụ + `labels_manual.csv` |
| | `label_auto.py`: matcher bằng regex arXiv ID và so khớp tiêu đề/tác giả/năm, gán nhãn 500 bài mỗi người | **Có** | `selection.csv` (dòng `auto`), `bibtex`, `references` | `labels_auto.csv` |
| | Đo độ chính xác của matcher trên bài thủ công | Chờ nhãn thủ công | `labels_manual.csv`, kết quả matcher | bảng chính xác |
| **Ngọc** (24127465) | `text_clean.py`, `pairs.py`: chuẩn hóa văn bản, tạo bảng cặp (entry, reference) cho từng bài | **Có**, không cần nhãn | `bibtex`, `references` | bảng cặp `m × n` |
| | `features.py`: đặc trưng tiêu đề, tác giả, năm, arXiv ID, đặc trưng tương đối trong nhóm | **Có**, không cần nhãn | bảng cặp | bảng đặc trưng |
| | Biểu đồ phân tích cho poster | Chờ | các bảng trên | hình |
| **Tạo** (24127534) | `evaluate.py`: hàm MRR@5 (viết và kiểm tra trên dữ liệu giả) | **Có** | quy tắc MRR@5 trong mục 3 | hàm + test |
| | `train.py`: baseline và LightGBM xếp hạng, bảng loại bỏ đặc trưng, phân tích lỗi | Chờ bảng đặc trưng và nhãn | đặc trưng, `labels_*.csv`, `selection.csv` | mô hình, báo cáo |
| | `predict.py`: sinh `predictions` | Chờ mô hình | mô hình, `selection.csv` | bảng `predictions` |
| **Vinh** (24127262) | `build_dataset.py`: lọc 5 bảng theo ID range từng người, ghi shard Parquet | **Có** | bảng M2 gộp, bảng M1 | 5 bảng cho mỗi range |
| | `validate_dataset.py`: kiểm tra schema, số dòng, ID | **Có** | các bảng trên | báo cáo kiểm tra |
| | `upload_hf.py`, dataset card, README, poster | Chờ `predictions` | 6 bảng, tài khoản HF | link dataset |

Nếu một người đảm nhận hai vai trò (ví dụ Vinh và Ngọc), người đó vẫn phải gán nhãn tay **5 bài trong mỗi ID range** (tức 10 bài), vì đề yêu cầu mỗi người gán trong ID range của mình. Điều này nên xác nhận lại với giảng viên.

### Định dạng file trao đổi giữa các bước **(đề xuất)**

Để các module khớp nhau, đề xuất thống nhất:

**`labels.csv`** (gộp thủ công và tự động, mỗi dòng là một entry đã gán)

| Cột | Ý nghĩa |
|---|---|
| `arxiv_id` | Bài chứa entry |
| `citation_key` | Key của entry trong bảng `bibtex` |
| `groundtruth_arxiv_id` | arXiv ID của reference đúng, để trống nếu không có reference tương ứng |
| `label_source` | `manual` hoặc `auto` |

Các cột `arxiv_id`, `citation_key`, `groundtruth_arxiv_id` trùng với bảng `predictions` của đề, nên có thể nối trực tiếp.

**`pairs`**: mỗi dòng là một cặp (entry, reference) của cùng một bài, khóa `(arxiv_id, citation_key, ref_arxiv_id)`. Đặc trưng nối vào bằng khóa này.

**Bảng `predictions`** (theo đề): `arxiv_id`, `partition` (`train`/`valid`/`test`), `citation_key`, `groundtruth_arxiv_id` (có thể rỗng), `cand_1` … `cand_5` (có thể rỗng). Chỉ chứa các bài thật sự dùng trong train/valid/test. Bài dự phòng chưa được thế chỗ và bài `manual_dropped` **không** đưa vào.

---

## 6. Quy trình gán nhãn thủ công

1. Mở danh sách 5 bài `manual` của mình trong `selection.csv` (mục 4).
2. **Gán các bài rủi ro trước** để còn kịp thay: những bài có `n_any_hit` ≤ 20 (xem bảng ở mục 4), đặc biệt bài test.
3. Với mỗi bài, duyệt từng reference arXiv và chọn entry bibtex tương ứng. Một entry chỉ khớp với một reference. Nhiều entry cùng trỏ về một reference (do bibliography lặp) vẫn hợp lệ.
4. Nếu bài gán xong **chưa đủ 20 cặp**, thay bằng bài dự phòng cùng người và **cùng tầng** (ưu tiên):

```powershell
python split.py --out-dir m3_selection_v3 --replace <bài_không_đủ> <bài_dự_phòng>
```

Bài dự phòng thừa hưởng đúng `partition` và `slot` của bài bị thay. Mỗi lần thay được ghi vào `selection_log.json`. Cập nhật `selection.csv` mới cho cả nhóm sau mỗi lần thay.

5. Khi đủ, kiểm tra: mỗi bài ≥ 20 cặp, tổng ≥ 100 cặp mỗi người.

Lưu ý rủi ro đã biết:

- `2512.19910` (test, Tạo) chỉ có ước lượng 17 cặp, cần tìm thêm cặp khớp mờ; nếu không đủ thì thay bằng `2512.19916` hoặc `2512.21255`.
- Một số bài sát ngưỡng (đúng 20): `2606.04623`, `2606.04955`, `2602.11395`, `2512.19906`.
- Dự phòng `2605.04816` (ước lượng 19) và `2602.11694` (ước lượng 13) có thể không đủ 20 cặp, nên cân nhắc khi thế chỗ.
- Công gán không đều: Vinh xem khoảng 382 entry (có bài 123 entry), Tạo khoảng 331, Khôi 243, Ngọc 219.

---

## 7. Mô hình và đánh giá

- Mỗi entry bibtex chỉ được so với các reference của **cùng bài** (ma trận `m × n`).
- Mô hình trả về 5 ứng viên tốt nhất. MRR@5 của một entry là `1/hạng` nếu reference đúng nằm trong top 5, ngược lại bằng 0; entry không có reference đúng thì không tính. MRR@5 là trung bình trên các entry có nhãn của tập test.
- Báo cáo **tách riêng** MRR@5 của test thủ công và test tự động. Lý do: bài tự động được chọn theo tín hiệu mà matcher regex dùng, nên test tự động dễ hơn và có phần vòng tròn.
- Test thủ công chỉ có 4 bài (khoảng 120 cặp ước lượng), nên MRR@5 có độ nhiễu lớn. Nên ghi rõ trong báo cáo.
- Độ khó đo bằng `ratio` là độ khó đối với regex, chưa chắc là độ khó đối với mô hình có đặc trưng tiêu đề và tác giả. Kết luận độ khó thật chỉ có sau khi chạy baseline.
- Đề cho phép dùng mô hình ngoài (LLM, pretrained) trong giới hạn Colab CPU, nhưng chấm tách khỏi các phương pháp ML truyền thống.

---

## 7b. Giới hạn đã biết của dữ liệu

- 31.914 entry thiếu title và 34.909 entry thiếu authors trong bảng `bibtex` (khoảng 3,5% và 3,9%), ảnh hưởng đặc trưng tiêu đề và tác giả.
- 118.168 cặp (bài, version, key) trích dẫn chưa phân giải được.
- 74 bài có key mâu thuẫn đã bị loại; 4 bài có version `timeout`/`no_root` cũng bị loại khỏi pool.
- Các con số này là tỷ lệ trường bị thiếu, không phải độ chính xác của parser.

---

## 8. Nộp bài

### Hugging Face dataset (mỗi người một dataset, theo ID range của mình)

Sáu bảng, dạng sharded Parquet, không dùng đối tượng lồng nhau hay JSON blob:

| Bảng | Nguồn | Cách làm |
|---|---|---|
| `papers`, `references` | M1 (`m1_tables/`) | Kiểm tra `arxiv_id` nằm trong range, rồi dùng |
| `elements`, `hierarchy`, `bibtex` | M2 gộp (`m2_full_v5`) | **Lọc theo `arxiv_id` trong range** rồi ghi shard mới |
| `predictions` | Bước mô hình | Chỉ các bài dùng trong train/valid/test của range đó |

**Không chạy lại M2 cho từng range.** Output M2 của mỗi bài không phụ thuộc bài khác, nên lọc từ bản chung là đúng. Không copy shard gốc, vì shard gốc được cắt theo 250 bài, không theo range, nên lẫn bài của nhiều người.

Kiểm tra sau khi lọc:

- Tổng số dòng của 4 range bằng bản chung: `elements` 9.913.475, `hierarchy` 13.309.173, `bibtex` 904.091.
- `arxiv_id` nhỏ nhất và lớn nhất mỗi bảng đúng với range.
- Mỗi range đủ 5.000 `arxiv_id`.

Dataset card (`README.md`) cần ghi: commit M2 `29a14fd` và `parser_sha256`, commit/seed của `split.py`, giới hạn đã biết ở mục 7b, ghi chú về 74 bài có key `...__candidate`.

### GitHub (1 repo chung)

Chỉ code (`src/`), `requirements.txt`, `README.md`. Không có dữ liệu. `selection.csv` là danh sách ID nhỏ, nên chia sẻ qua Drive hoặc commit tùy nhóm thống nhất, còn `candidates.csv` thì không đưa lên Git.

### Poster

1 file `.pptx`, 1 trang, A2 dọc. Nội dung: đặc trưng, kiến trúc mô hình, kết quả MRR@5, số liệu nổi bật của pipeline M2 và M3.

---

## 9. Checklist

**Việc cần làm ngay (người giữ dữ liệu)**
- [ ] Đổi tên và lưu `selection.csv`, `selection_meta.json` của v3
- [ ] Chia sẻ thư mục Drive (M2 `m2_full_v5`, `m3_selection_v3`, `m1_tables`) cho cả nhóm
- [ ] Gửi tài liệu này cho cả nhóm, xác nhận trạng thái `label_tool.py` với Khôi

**Dữ liệu và code**
- [ ] `candidates.csv` và `selection.csv` khớp `candidates_sha256` trong `selection_meta.json`
- [ ] Mọi người dùng cùng bản M2 `m2_full_v5` (commit `29a14fd`)
- [ ] Không ai chạy lại `split.py` sau khi đã chia sẻ

**Gán nhãn**
- [ ] Mỗi bài thủ công đạt ≥ 20 cặp, mỗi người ≥ 100 cặp
- [ ] Bài không đạt đã được thay bằng dự phòng, `selection_log.json` đã cập nhật
- [ ] Nhãn tự động cho 500 bài mỗi người đã sinh

**Mô hình và nộp**
- [ ] `predictions` đủ train/valid/test, có `cand_1`–`cand_5`
- [ ] 6 bảng của mỗi range qua `validate_dataset.py`
- [ ] Mỗi người upload dataset và dán link công khai
- [ ] Poster A2 dọc
