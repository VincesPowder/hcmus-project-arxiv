# Labelling Module

Áp dụng phản hồi TA tại [QnA M3](https://docs.google.com/spreadsheets/d/1D9JuNExdR3Mh0_8G1thL2p-7fMevDy7uOMPlnh_p51Q/edit#gid=1061532440)

Không chạy lại `split.py`. Dùng `selection.csv` đã chốt; công cụ không sửa selection,
meta hay dữ liệu nguồn. Các đường dẫn dưới đây là ví dụ, thay bằng dữ liệu thực tế.
Chỉ dùng M2 `m2_full_v5` đã xác minh; không dùng các bản range cũ.

Cài dependencies đã có trong `src/requirements-m2.txt` (pyarrow cho Parquet).
Chạy từ root repo, Python 3.10+. SQLite và matcher dùng thư viện chuẩn.
Chuẩn bị thư mục output riêng trong repo (ví dụ `work_m3/`, không commit).
Nguồn M1 có thể truyền nhiều thư mục/file để đọc cả bốn range.

```bash
mkdir -p work_m3
python src/label_tool.py --selection src/tools/m3_selection/selection.csv --owner 24127192 --bibtex DATA/exports/tables/bibtex --references DATA/m1_tables/references.parquet --cache work_m3/index.sqlite --output work_m3/labels_manual.csv
python src/label_auto.py --selection src/tools/m3_selection/selection.csv --owner 24127192 --bibtex DATA/exports/tables/bibtex --references DATA/m1_tables/references.parquet --cache work_m3/index.sqlite --output work_m3/labels_auto.csv
```

Bỏ `--owner` để xử lý cả nhóm. Các cột ID luôn được đọc dạng chuỗi.
Hỗ trợ Parquet shards hoặc CSV có đúng schema M1/M2; authors trong CSV là JSON list.
Không đọc các bảng elements/hierarchy. Cache SQLite có chỉ mục theo bài; đọc input
batch 8192 dòng, tái sử dụng khi đường dẫn/kích thước/mtime không đổi. Thay dữ liệu
mà giữ nguyên cả kích thước và mtime thì cần dùng cache mới. Duplicate có metadata
mâu thuẫn sẽ báo lỗi thay vì âm thầm chọn một dòng.

## Công cụ và thư viện được sử dụng

Bộ công cụ gán nhãn được viết riêng cho M3 bằng Python, chạy trong terminal.
Người gán nhãn quyết định nhãn thủ công; điểm gợi ý chỉ hỗ trợ tìm entry phù hợp.

| Thành phần | Vai trò |
|---|---|
| `src/label_tool.py` | Giao diện dòng lệnh để duyệt reference, chọn entry, kiểm tra gán lại và lưu nhãn thủ công. |
| `src/label_auto.py` | Sinh nhãn tự động cho danh sách đã chọn, xuất nhãn và file audit để rà soát. |
| `src/m3/labelling.py` | Logic dùng chung: đọc dữ liệu, lọc selection, lập chỉ mục, chuẩn hóa, tính điểm và ghi CSV. |
| `pyarrow.dataset` | Đọc các shard Parquet M1/M2 theo batch; đây là thư viện bên ngoài cần cài. |
| `sqlite3` | Lưu chỉ mục dữ liệu cục bộ theo loại bảng, bài và citation key/reference ID. |
| `re`, `unicodedata` | Trích arXiv ID; chuẩn hóa Unicode NFKD, chữ hoa/thường, lệnh LaTeX và dấu phân cách. |
| `difflib.SequenceMatcher` | Tính độ tương tự chuỗi tiêu đề, với `autojunk=False`. |
| `csv`, `json`, `hashlib`, `pathlib` | Đọc/ghi dữ liệu, tạo fingerprint cache và quản lý đường dẫn. |
| `unittest` | Kiểm thử logic matcher và khả năng lập chỉ mục dữ liệu giả. |

Ngoài PyArrow, các thư viện trên thuộc thư viện chuẩn Python. Matcher hiện tại
là phương pháp dựa trên quy tắc; chưa sử dụng LLM, embedding, dịch vụ gán nhãn
bên ngoài hoặc mô hình học máy đã huấn luyện.

### Cách tính điểm gợi ý và chọn nhãn

Với mỗi cặp entry–reference của cùng bài, matcher thực hiện:

1. Trích ID từ `url`, `doi`, `title`, `citation_key` của entry, so với
   `ref_arxiv_id`. Một ID duy nhất khớp được điểm 1 và được nhận nhãn tự động.
   Nếu entry có ID nhưng không khớp, không dùng so khớp mờ để ghi đè tín hiệu đó.
2. Nếu entry không có ID, tính điểm metadata:

   `score = 0.85 × max(Jaccard_title, SequenceMatcher_title) + 0.10 × author_overlap + 0.05 × year_ok`

   `Jaccard_title` là tỷ lệ giao/hợp của tập token tiêu đề. `author_overlap`
   là số họ tác giả chung chia cho số họ của phía ít tác giả hơn (mẫu số tối
   thiểu 1); họ được xấp xỉ bằng token cuối của tên đã chuẩn hóa. `year_ok`
   bằng 1 nếu năm entry và năm trong `ref_submission_date` lệch không quá 1.
3. Nhận nhãn metadata chỉ khi tiêu đề entry có ít nhất 4 token khác nhau,
   tiêu đề chuẩn hóa trùng hệt hoặc độ tương tự chuỗi đạt 0.94, có họ tác giả
   chung và năm phù hợp. Đồng thời điểm phải đạt `--threshold` (mặc định 0.94)
   và hơn ứng viên thứ hai ít nhất `--margin` (mặc định 0.04).

Công cụ thủ công xếp hạng entry cho từng reference bằng điểm trên, nhưng cho
phép người dùng chọn bất kỳ citation key nào. Công cụ tự động xếp hạng reference
cho từng entry và chỉ nhận kết quả khi thỏa điều kiện. Nhiều entry cùng khớp một
reference là hợp lệ; một entry chỉ lưu một reference đúng.

So khớp họ bằng token cuối có thể sai với tên nhiều thành phần hoặc thứ tự tên
khác nhau. Năm xuất bản và năm nộp arXiv cũng có thể lệch hơn một năm. Các trường
hợp này cần rà soát thủ công; điểm cao chưa bảo đảm nhãn đúng.

### File kết quả và các entry chưa khớp

| File | Nội dung |
|---|---|
| `labels_manual.csv` | Chỉ các cặp khớp do người dùng xác nhận; entry chưa gán không xuất hiện. |
| `labels_auto.csv` | Chỉ các cặp được matcher nhận; mọi groundtruth đều có giá trị. |
| `labels_auto.csv.audit.csv` | Bốn cột nhãn, thêm `score` và `reason` của ứng viên đứng đầu để rà soát. |
| `labels_auto.csv.papers.csv` | Số entry, cặp khớp, abstention và trạng thái từng bài được xử lý. |
| `index.sqlite` | Cache cục bộ có thể tái tạo từ nguồn M1/M2, không phải sản phẩm nộp. |

Trong audit, `id` và `metadata` là nhãn được nhận; các lý do như
`abstain_review`, `abstain_id_conflict`, `abstain_missing_title` biểu thị chưa
nhận nhãn. Groundtruth rỗng chỉ xuất hiện trong audit để ghi nhận abstention,
không đưa vào labels hoặc coi là bằng chứng không có reference đúng.
Công cụ báo lỗi nếu bài cần xử lý thiếu bibtex hoặc references trong nguồn đầu vào.

## Gán thủ công

Duyệt từng reference, hiện 5–10 gợi ý (`--top`), tiêu đề/tác giả/năm và nhãn đã gán.
Nhập số (hoặc nhiều số cách nhau bởi dấu phẩy) hay `key:CITATION_KEY`.
`clear:KEY` xóa nhãn gán nhầm và lưu ngay; entry đó cần được duyệt lại.
`all` hiện toàn bộ entry; `skip` bỏ qua reference; `quit` lưu và thoát.
Một reference có thể khớp nhiều entry. Đổi một entry sang reference khác cần nhập
`REASSIGN` sau cảnh báo. Mỗi lần chọn lưu CSV nguyên tử; chạy lại cùng lệnh để
xem và sửa nhãn đã lưu. Công cụ duyệt lại các reference để tránh bỏ sót; không tự
coi một bài đã hoàn tất. Không còn thao tác `FINALIZE`: sau khi duyệt reference,
công cụ lưu các cặp đã chọn; các entry còn lại giữ trạng thái chưa gán.
Không cần duyệt hoặc tạo nhãn cho mọi entry để bài có đủ số cặp hợp lệ.
Sau mỗi bài kiểm tra >=20 cặp, tổng mỗi owner >=100. Nếu thiếu, thực hiện quy trình
thay dự phòng cùng owner/tier trong hướng dẫn nhóm, dùng output mới sau khi thay
selection (output cũ có bài ngoài selection sẽ bị từ chối).

## Gán tự động và quy mô

Mặc định chỉ xử lý kind=auto, partition=train/valid/test (500 bài/người).
Không gán bài manual/reserve/dropped. `--all-papers` là chế độ chẩn đoán trên toàn
bộ bài trong index trừ mọi bài manual/reserve/dropped; không dùng output này làm
nhãn nộp thay cho danh sách auto đã chốt.

Ưu tiên một arXiv ID duy nhất trùng reference cùng bài (bỏ hậu tố phiên bản).
ID mâu thuẫn/đa ID bị từ chối. Fallback dùng title + surname tác giả + năm ±1,
ngưỡng mặc định .94 và khoảng cách với ứng viên thứ hai >=.04. Thiếu tín hiệu
hoặc mơ hồ sẽ không xuất nhãn; audit ghi groundtruth rỗng với lý do abstention.
`--threshold` và `--margin` chỉ hiệu chỉnh trên train/valid, không dùng test.
CSV audit lưu score/reason để phân biệt abstention. Output chính đúng 4 cột đề xuất:
`arxiv_id,citation_key,groundtruth_arxiv_id,label_source`.
Không suy diễn nhãn từ các bài khác và không dùng nhãn tự động làm nhãn thủ công.

Input được index một lần trên đĩa; chỉ giữ entry/reference của một bài trong RAM,
output ghi streaming. Chi phí matcher O(sum(m*n)), không tạo ma trận toàn corpus;
chuẩn hóa reference trước vòng lặp entry. Không tải mô hình hoặc gọi mạng.
Chạy lại auto tái sử dụng index nhưng tính lại nhãn; chỉ thay CSV chính sau khi
hoàn tất tính toán. Nếu bài nào không tìm được cặp, vẫn lưu kết quả và audit để
rá soát nhưng thoát mã khác 0, báo chưa hoàn tất. Không thay bài hoặc nới ngưỡng
ngầm. Chỉ tính bài đã có ít nhất một cặp hợp lệ là bài auto có nhãn; đây là điều
kiện tối thiểu để đếm bài, không phải ngưỡng chất lượng do TA quy định.
Không có resume theo bài cho auto; manual lưu sau từng lựa chọn.

Đã có test ID/version, ID conflict, abstention/tie, metadata và index 20.000 bài giả.
Đây không phải benchmark 20.000 bài thật. Cần đo tốc độ, RAM và precision trên nhãn
thủ công thực tế; chưa có dữ liệu Drive để xác nhận. Không coi score là xác suất.
Báo cáo độ chính xác matcher chỉ trên cặp có nhãn thủ công đã duyệt, kèm coverage;
không dùng output auto để chứng minh độ chính xác của chính matcher.


## Kiểm tra bàn giao và đo độ chính xác matcher

`src/check_labels.py` hoàn thành phần đo độ chính xác matcher của Khôi. Công cụ
chạy lại matcher với tham số cố định trên bài manual rồi so với quyết định của
người gán nhãn. Không ghi đè nhãn thủ công và không sinh nhãn manual từ matcher.
Ưu tiên duyệt độc lập trước khi xem kết quả matcher để giảm thiên lệch gợi ý.

Sau khi gán đủ 5 bài (mỗi bài >=20 cặp, tổng >=100), chạy:

```bash
python src/check_labels.py --selection src/tools/m3_selection/selection.csv --owner 24127192 --bibtex DATA/exports/tables/bibtex --references DATA/m1_tables/references.parquet --cache work_m3/index.sqlite --manual work_m3/labels_manual.csv --output work_m3/labels.csv
```

Thêm `--auto work_m3/labels_auto.csv` để kiểm tra 500 bài auto và gộp thành
`work_m3/labels.csv`. Bỏ `--owner` nếu cung cấp nhãn của toàn nhóm. Công cụ kiểm
tra số bài theo owner, key trùng/không tồn tại, source sai, reference ngoài cùng
bài, bài ngoài selection và ngưỡng cặp thủ công. Không yêu cầu nhãn mọi entry.
Mỗi bài auto trong selection phải có cặp khớp; báo cáo tách số bài được chọn với
số bài thực sự có nhãn. Labels manual/auto có groundtruth rỗng đều bị từ chối.

Chỉ xuất file gộp khi manual và auto đều hợp lệ. Nếu không hợp lệ, thoát mã 1,
đọc danh sách lỗi trong report; file gộp cũ (nếu có) không được cập nhật. Không dùng
file cũ khi report mới báo FAIL. Chế độ chỉ có `--manual` không xuất file gộp.

Các file báo cáo theo tiền tố `--output`:

| Hậu tố | Ý nghĩa |
|---|---|
| `.report.json` | PASS/FAIL, lỗi, số cặp từng bài/owner, metrics, tham số, SHA-256 selection/nhãn/code matcher và fingerprint cache nguồn. |
| `.metrics.csv` | Bảng metrics toàn bộ, từng owner, từng partition và owner–partition. |
| `.matcher.csv` | Chi tiết truth/predicted/score/reason từng entry manual. |

Metrics chỉ được tính trên các cặp manual hợp lệ, sau khi kiểm tra đủ quota:

- **Precision trên tập có nhãn** = số dự đoán đúng / số dự đoán được nhận trên
  các entry đã có cặp manual.
- **Recall** = số dự đoán đúng / số cặp manual.
- **Coverage trên tập có nhãn** = số dự đoán được nhận / số cặp manual.
- **Accuracy trên tập có nhãn** = số dự đoán đúng / số cặp manual; bằng recall
  trong thiết kế chỉ có nhãn matching này. Không có true negative.

Entry không có nhãn manual bị loại khỏi phép đo, không suy diễn là nhãn âm.
Precision/coverage này chỉ có ý nghĩa trên tập cặp đã biết; không chứng minh
precision toàn bộ output auto ở các entry chưa được người dùng kiểm tra.
Abstention hoặc chọn sai reference trên một cặp manual đều làm giảm recall.
Mẫu số bằng 0 cho JSON `null` và ô CSV rỗng. Các file `.matcher.csv` chỉ phục vụ
chẩn đoán matcher, không nhập vào labels_auto hay dữ liệu train của bài manual.
Không phải MRR@5 và không thay phần đánh giá mô hình của Tạo. Dùng train/valid
để hiệu chỉnh; giữ cố định tham số trước khi báo cáo test.

Có thể thêm `--meta src/tools/m3_selection/selection_meta.json --candidates DATA/candidates.csv`
để kiểm tra SHA-256 candidates với meta mà không chạy split. Hash này xác nhận
đầu vào candidates; không chứng minh selection được sinh đúng thuật toán hoặc
thay thế kiểm tra nguồn gốc bản v3. Fingerprint cache dựa trên file stat, không
phải checksum nội dung Parquet.

Kiểm thử hiện tại:

```bash
python -m unittest discover -s tests -p 'test_m3*.py' -v
```

Đã kiểm thử luồng auto → kiểm tra → gộp trên dữ liệu giả 5 manual/500 auto,
precision/recall khi dự đoán sai hoặc abstain, từ chối nhãn trùng và reference
ngoài bài, cùng kiểm tra index 20.000 bài giả. Còn cần nhãn người thật và dữ liệu
M1/M2 thật trong phạm vi thư mục repo được cho phép để hoàn tất kết quả thực tế.


## Chuyển từ output phiên bản cũ

Sao lưu nhãn cũ trong `work_m3/`, rồi tạo bản labels mới bằng cách giữ các dòng
có `groundtruth_arxiv_id` khác rỗng. Không xóa các cặp đã gán. Dùng bản mới để
resume hoặc kiểm tra; module báo lỗi rõ ràng nếu gặp dòng nhãn rỗng kiểu cũ.
Chạy lại auto với tên output mới để có audit và thống kê từng bài đồng bộ.
Không đưa các file nhãn, cache hay audit lên GitHub.

Khi tạo predictions ở bước sau: dùng các cặp manual đã gán; giữ trạng thái entry
chưa có nhãn tách khỏi groundtruth. MRR@5 chỉ tính trên cặp có nhãn matching.
Ứng viên thuộc references cùng bài; nếu ít hơn 5 thì điền null các cand_* thiếu.
Thực nghiệm che ID/URL/DOI là kết quả phụ, không thay kết quả chính theo đề.
