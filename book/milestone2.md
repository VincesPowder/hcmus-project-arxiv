# Milestone 2 — Hierarchical Parsing and Standardization

## Phạm vi và mục tiêu

Đầu vào là toàn bộ **20.000 publication của bốn thành viên** tại [Intro](intro.md), gồm mọi version đã phát hiện trong Milestone 1. Mục tiêu là ba bảng `elements`, `hierarchy`, `bibtex` để dùng tiếp trong Milestone 3. Corpus và output xử lý được giữ ngoài Git.

Báo cáo đối chiếu với đề **Milestone 2 — Hierarchical Parsing and Standardization**, mục Description, Requirement và Report Requirements (Jupyter Book). [Đề gốc](https://hcmus-cs-ta.github.io/lab-book/intro-ds/statement/parsing/).

**Trạng thái số liệu:** 20.000 publication là phạm vi được giao, không phải số publication đã được chứng minh parse thành công. Chưa có bộ output Milestone 2 đầy đủ và benchmark Colab CPU được đưa vào bản báo cáo này. Các bảng dưới sinh trong lúc build từ output có sẵn nếu được cung cấp, còn thiếu được ghi rõ. Số liệu crawling ở Milestone 1 không thay thế số liệu parsing.

## 1. Multi-file gathering và cấu trúc phân cấp

Pipeline thực tế là `src/pipeline.py` → `gather.gather_version` → `hier_parser.parse_document` → `refs.extract_references` → dedup references/đổi citation keys → `assemble.PaperAssembler` → Parquet.

`gather.py` tìm compilation root chứa `\documentclass`, bỏ comment trước khi phân tích lệnh và theo `\input`/`\include` đệ quy. Chỉ các file reachable từ root được ghép vào luồng văn bản; file `.tex` thừa được ghi trong `unreachable`. File include thiếu và vòng lặp được ghi warning thay vì âm thầm đọc toàn bộ thư mục. Nhiều ứng viên root được chọn bằng heuristic; đây là một nguồn sai số cần kiểm tra thủ công.

`hier_parser.py` dùng stack các mức `part → chapter → section → subsection → subsubsection → paragraph → subparagraph`. Khi gặp heading mới, parser lùi stack tới ancestor có mức nhỏ hơn; không giả định tài liệu luôn có chapter hay section. `\section*` vẫn là heading, acknowledgements và appendix được giữ lại.

| Loại node | Vai trò và nội dung |
|---|---|
| `document` | Root riêng cho từng version |
| `heading` | Thành phần phân cấp theo heading thực tế |
| `list` → `item` | Mỗi item là nhánh con, có thể chứa câu/list lồng nhau |
| `sentence` | Lá văn xuôi, chia theo dấu chấm kết thúc câu |
| `formula` | Lá block math đã chuẩn hóa |
| `figure` | Lá figure hoặc table, giữ nội dung/caption ngữ nghĩa |

Sentence splitting bảo vệ inline math, lệnh LaTeX, URL, số thập phân và các viết tắt như `i.e.`, `et al.`, `Fig. 1`. Bibliography không nằm trong hierarchy; nó được xử lý riêng thành bảng `bibtex`. Parser là heuristic xử lý source, không chạy TeX engine nên không bảo đảm tái hiện mọi macro/conditional tùy biến.

## 2. Cleanup và chuẩn hóa toán học

Các lệnh trình bày không mang nghĩa như `\centering`, placement `[htpb]`, `\midrule` được loại bỏ; phần văn bản trong các lệnh định dạng được giữ lại. Inline math dùng `$...$`; display math từ `$$...$$`, `\[...\]` và các environment toán được đưa về `\begin{equation}...\end{equation}`. Sau cleanup, nội dung được chuẩn hóa Unicode NFC và gộp khoảng trắng trước khi so sánh.

## 3. Trích xuất và dedup references

`refs.py` lấy `\bibitem` chỉ từ compilation path và `.bib` chỉ từ các thư viện được khai báo. `.bib` được đọc thành các trường title, authors, year, DOI, journal và URL. Với `--bib-scope cited` mặc định, chỉ giữ các entry được cite hoặc toàn bộ thư viện khi có `\nocite{*}`; `--bib-scope all` giữ mọi entry trong thư viện đã khai báo.

### Tách title và authors trong `\bibitem`

Ưu tiên các trường `\bibinfo{author}`/`\bibinfo{title}`, sau đó ranh giới `\newblock`, title có dấu ngoặc kép/định dạng, rồi heuristic câu văn. Tên viết tắt như `A.` không được dùng làm ranh giới tác giả/title. Kiểu author-year `(2020)` được xử lý mà không cắt mất phần title đứng sau năm; apostrophe trong tên như `O'Connor` không được coi là dấu mở title. Nếu title không thể tách tin cậy, để trống title/authors thay vì gán cả reference thành tên tác giả. DOI, URL, citation key và năm vẫn được giữ nếu nhận diện được.

Ví dụ minh họa, không phải số liệu đánh giá corpus:

```latex
\bibitem{k} A. Vaswani and N. Shazeer.
Attention is all you need. NeurIPS, 2017.
```

Output mong đợi: `authors = ["A. Vaswani", "N. Shazeer"]`, `title = "Attention is all you need"`, `year = 2017`, `entry_type = "misc"`. Văn phong không có ranh giới rõ hoặc citation thiếu metadata vẫn là hạn chế; không suy diễn độ chính xác 100%.

### Hợp nhất xuyên version

`unify_bibtex` nhóm theo title chuẩn hóa, fallback theo citation key khi thiếu title. Giữ một key đại diện; điền các trường đang thiếu từ bản trùng, chọn title và danh sách authors dài hơn. Đây là field union theo quy tắc ưu tiên, không phải ghép vô điều kiện mọi giá trị xung đột. `rename_cites` đổi `\cite`, `\citep`, `\citet` sang key đại diện trước khi dedup full text. Ví dụ `vaswani2017` ở v1 và `vit_transformer` ở v2 có cùng title thì giữ một key, bổ sung DOI từ v2 và giữ năm có sẵn ở v1.

Title giống nhau nhưng khác công trình có thể bị gộp nhầm; title bị lỗi OCR/markup hoặc thay đổi mạnh có thể không được gộp. DOI không phải khóa matching chính hiện tại. Cần đánh giá thủ công một mẫu references trước khi dùng cho matching ở Milestone 3.

## 4. Dedup nội dung và output schema

`PaperAssembler` giữ một element store cho từng publication qua mọi version. Identity gồm `(kind, cleaned content, occurrence index)`; occurrence index giữ hai câu/heading lặp trong cùng version thành hai node khác nhau để cây không có hai parent. Nội dung tương ứng giống hệt ở version khác dùng chung `element_id`; root từng version vẫn riêng. Chính sách này khắt khe hơn full-text-only khi nội dung lặp và số lần xuất hiện thay đổi giữa version.

Ví dụ: câu `We study the citation matching problem` dùng `2310-12345#s1` trong cả v1 và v2. Edge của v1 trỏ tới `#h1`; edge của v2 trỏ tới `#h2`. Element không cần được sao chép chỉ vì parent thay đổi.

| Bảng | Columns và types |
|---|---|
| `elements` | `element_id: string!`, `arxiv_id: string!`, `kind: dictionary<string>`, `content: string` |
| `hierarchy` | `arxiv_id: string!`, `version: int32`, `element_id: string!`, `parent_id: string!` |
| `bibtex` | `arxiv_id: string!`, `citation_key: string!`, `entry_type: dictionary<string>`, `title: string`, `authors: list<string>`, `year: int32 nullable`, `doi: string`, `journal: string`, `url: string` |

`!` nghĩa là non-null. Root có trong `elements` nhưng không có edge parent. ID bắt đầu bằng publication key. Output chia shard Parquet Zstandard; JSON theo paper lưu đủ dữ liệu để dựng lại bảng mà không parse source lần nữa.

## 5. Coverage và thống kê trước/sau dedup

Mỗi version giữ một trạng thái: `parsed`, `empty`, `not_downloaded`, `no_root`, `timeout`, `parse_error`. Version không TeX tạo record `empty` và không tạo parsed elements. Không loại `empty` khỏi mẫu số coverage toàn corpus. `not_downloaded` và paper output còn thiếu phải được báo riêng, không tính thành thành công.

Các bảng/biểu đồ bên dưới do extension sinh khi compile; extension chỉ đọc JSON đã xử lý, không chạy parser/crawler. Coverage theo từng publication và version được xuất thành bảng tải xuống. Tỷ lệ parse tính cả trên tổng version đã quan sát và trên các version không `empty`; cả hai mẫu số đều được hiển thị. Publication chưa có output được đếm riêng so với mục tiêu 20.000.

```{include} _generated/m2_statistics.md
```

## 6. Hiệu năng và tái lập trên Colab CPU

Testbed yêu cầu là Google Colab **CPU-only** (Runtime → Change runtime type → CPU). Cần ghi Python, CPU, RAM, workers, commit SHA, thời điểm và trạng thái fresh/resume. Không dùng kết quả smoke test máy local hoặc số crawl để điền benchmark cuối.

Lệnh dưới là hướng dẫn tái lập, không được gọi khi build book:

```bash
pip install -r src/requirements.txt
git rev-parse HEAD
python src/pipeline.py --corpus-dir /content/corpus \
  --out-dir /content/parsed_m2 --work-dir /content/work_m2 \
  --workers 2 --timeout 120 --bib-scope cited
```

Để benchmark fresh, dùng output directory mới cho mỗi range. Parser mặc định resume paper đã có JSON; sau sửa reference parser cần dùng output mới hoặc `--force` để kết quả phản ánh mã mới. Báo cáo này không chạy lại corpus. `--merge-only` chỉ dựng bảng, không phải benchmark parser đầy đủ.

Các trường `wall_seconds`, `avg_seconds_per_paper`, `max_ram_mb`, `output_json_bytes`, `tables_bytes` đến từ `parse_metrics.json`. Average hiện tại của pipeline là thời gian parse chia số paper không skipped; báo cáo phân biệt nó với end-to-end wall time chia số publication thực xử lý. ResourceMonitor cộng RSS process chính và các child process đệ quy, lấy mẫu mỗi giây; peak giữa hai lần lấy mẫu có thể bị bỏ lỡ và RSS có thể đếm lặp trang bộ nhớ chia sẻ. Với bốn lượt chạy: tổng wall time là tổng công việc nối tiếp; peak RAM lấy max, không cộng; dung lượng JSON và Parquet báo riêng để tránh hiểu là một bản dữ liệu.

## 7. Đánh giá, giới hạn và việc còn thiếu

| Yêu cầu đề | Bằng chứng trong source/report | Trạng thái cần xác minh |
|---|---|---|
| Compilation path, bỏ source thừa | `gather.py`; mục 1 | Kiểm tra mẫu nhiều root/macro includes |
| Heading, list, sentence, formula, figure/table | `hier_parser.py`; mục 1–2 | Đối chiếu với PDF trên mẫu đa phong cách |
| Bỏ references, giữ appendix/acknowledgements | Parser; mục 1 | Kiểm tra heading tùy biến |
| Reference extraction, field union, cite renaming | `refs.py`; mục 3 | Kiểm tra mẫu `\bibitem`, trường xung đột |
| Full-text dedup xuyên version | `assemble.py`; mục 4 | Kiểm tra lặp node và thay đổi cấu trúc |
| Ba bảng đúng schema | `pipeline.TableWriter`; mục 4 | Kiểm tra output đủ bốn range |
| Coverage từng publication, counts trước/sau | Bảng sinh ở mục 5 | Cần output M2 đầy đủ |
| Colab CPU wall time/RAM/storage | Mục 6 | Chưa có benchmark cuối xác nhận |

Các hạn chế còn lại gồm macro TeX phức tạp, environment tùy biến, year/title mơ hồ trong references, dedup theo title có thể false-positive và peak RAM phụ thuộc khoảng lấy mẫu. Cần cung cấp output của đủ bốn range và benchmark Colab CPU trước khi khẳng định hoàn tất xử lý 20.000 bài.
