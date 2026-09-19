# Ảnh minh chứng — cách chúng được tạo ra

Mọi ảnh trong thư mục này đều chụp từ phần mềm thật đang chạy hoặc từ một lần chạy
thật, ở trạng thái do chính MCP server trong repo này tạo ra. Không có ảnh nào là
dựng lại, ghép hay vẽ minh hoạ.

Hai script trong [`scripts/`](../../scripts/) làm việc này và chạy lại được:

| Script | Việc |
|---|---|
| [`capture-window.ps1`](../../scripts/capture-window.ps1) | Chụp một cửa sổ ứng dụng ra PNG, kể cả khung nhìn tăng tốc phần cứng |
| [`redact-image.ps1`](../../scripts/redact-image.ps1) | Che vùng chữ nhận diện và cắt ảnh trước khi đưa lên repo công khai |

---

## `civil3d-tin-surface.png`

**Nội dung:** một bề mặt TIN 240 × 60 m (325 điểm, 576 tam giác) hiển thị bằng
style *Contours and Triangles*, một tuyến chạy dọc tim với nhãn lý trình
0+000 → 0+240, và bên dưới là profile view với đường trắc dọc lấy từ chính bề mặt
ấy. Toàn bộ do MCP dựng vào một bản vẽ mới; không mở file dự án nào.

Chuỗi tool đã dùng, theo đúng thứ tự:

```
create_new_drawing(template_path=".../_Autodesk Civil 3D (Metric) NCS.dwt")
build_surface_from_xyz_file(
    surface_name="HOANCONG", xyz_path="hoancong.xyz",
    max_triangle_length=15, style="Contours and Triangles")
create_alignment(name="TIM_TUYEN", points=[[0,30,0],[240,30,0]])
create_profile_from_surface(
    alignment="TIM_TUYEN", surface="HOANCONG", profile_name="TRAC_DOC_HOANCONG")
create_profile_view(name="KHUNG_TRAC_DOC", alignment="TIM_TUYEN", origin=[0,-120])
send_civil3d_command("_.ZOOM _E _.ZOOM 0.92x")
```

File `hoancong.xyz` là 325 điểm sinh bằng công thức, để con số nào cũng kiểm lại
được bằng tay: nền đường dài 240 m rộng 60 m, dốc dọc 2 %, mui luyện 2 % về hai
mép, cộng một gợn sóng hình sin để mặt TIN không phẳng tuyệt đối.

```python
z = 10 + 0.02*x - 0.02*abs(y-30) + 0.35*sin(x/38) + 0.12*cos(y/11)
```

Server tự kiểm chứng từng bước và trả về số đo chứ không trả về số đã gửi:
`points_added_measured` = 325 lấy từ chênh lệch `Statistics.NumberOfPoints`
trước/sau, `max_triangle_length` = 15 đọc lại từ `DefinitionProperties`, và trắc
dọc được xác nhận bằng chiều dài 240 m cùng biên độ cao độ 9,89 – 14,702 m.

### Hai điều đáng ghi lại từ lần dựng này

**`template_path` phải là đường dẫn đầy đủ, không phải tên file.** Truyền tên file
trần thì Civil 3D lặng lẽ dùng template mặc định, và bản vẽ nhận được chỉ có đúng
một surface style tên `Standard` — style ấy không hiển thị gì cả. Triệu chứng là
một bản vẽ trông như trống rỗng, chứ không phải một thông báo lỗi. Khoá `template`
trong kết quả trả về `null` chính là dấu hiệu: nó lặp lại đường dẫn đã dùng, nên
`null` nghĩa là "không có template nào được dùng".

**Tên style phải có thật trong bản vẽ.** Style không tồn tại thì `AddTinSurface`
trả `E_INVALIDARG`, cùng mã lỗi với trường hợp thiếu `BaseLayer` — nên thông báo
lỗi không chỉ đúng nguyên nhân. Danh sách style có thật đọc được bằng
`doc.SurfaceStyles`; template Metric NCS có 14 style, trong đó
*Contours and Triangles* là style cho ra hình rõ nhất cho một ảnh minh chứng.

### Chụp và che

```powershell
.\scripts\capture-window.ps1 -ProcessId <PID của acad> `
    -OutFile $env:TEMP\c3d-shot.png -DelaySeconds 4

.\scripts\redact-image.ps1 -InFile $env:TEMP\c3d-shot.png `
    -OutFile $env:TEMP\c3d-redacted.png `
    -Region '1240,12,180,34,#2F3033,nguoi-dung','150,242,130,28,#2F3033,Drawing1'
```

Hai vùng che là **tên tài khoản Autodesk đang đăng nhập** ở góc trên phải, và
**tab của một bản vẽ dự án thật** đang mở trong cùng phiên. Cả hai đều không phải
chủ đề của ảnh, và cả hai đều là thông tin nhận diện.

Ảnh cuối được thu về 1440 px và giảm còn bảng 256 màu cho nhẹ:

```python
from PIL import Image
im = Image.open("c3d-redacted.png").convert("RGB")
im = im.resize((1440, round(im.height*1440/im.width)), Image.LANCZOS)
im.quantize(colors=256, dither=Image.FLOYDSTEINBERG).save(
    "docs/images/civil3d-tin-surface.png", optimize=True)
```

---

## `install-check-and-tests.png`

**Nội dung:** ba lệnh mà CI chạy — `install.py --check` nạp thử server, đếm được
58 tool và dò ra Civil 3D 2026 cùng phiên bản COM 13.8; `ruff` sạch; bộ test
offline xanh.

```powershell
python install.py --check | Select-String '\[OK\]|\[!\]|\[X\]'
ruff check .
pytest
```

`install.py` in ra các tiêu đề bước cách nhau bằng đường kẻ, dài hơn một màn hình
24 dòng. Bộ lọc chỉ giữ lại các dòng trạng thái để cả ba lệnh cùng nằm trong một
khung hình; chạy `python install.py --check` trần cho ra đúng những dòng đó kèm
tiêu đề bước.

Đường dẫn trong ảnh là `X:\` vì repo được ánh xạ qua một ổ ảo trước khi chụp, để
ảnh không mang theo tên người dùng của máy:

```powershell
subst X: <thư mục repo>
# chạy 3 lệnh trong X:\ rồi chụp
subst X: /d
```

**Cửa sổ tự chụp chính nó.** Script chạy ba lệnh rồi gọi `capture-window.ps1` với
`-ProcessId $PID` ở dòng cuối, thay vì để một tiến trình bên ngoài chụp nó. Lý do
nằm ở cách chụp: ảnh được copy từ vùng màn hình mà cửa sổ đang chiếm, nên cửa sổ
phải thật sự ở tiền cảnh. Tiến trình bên ngoài phải giành tiền cảnh, và Windows
thường từ chối — đã đo được: lời gọi thất bại, script vẫn chụp, và file PNG chứa
một ứng dụng khác hẳn. Chạy từ bên trong thì cửa sổ đã ở tiền cảnh sẵn.

**Con số test trong ảnh là ảnh chụp tại một thời điểm.** Tài liệu cố tình không
nhắc lại con số đó ở bất kỳ đâu khác: grep và test giữ đồng bộ được mọi bản sao
của một dữ kiện, trừ bản nằm trong pixel. Thêm test thì ảnh này lỗi thời — chụp
lại nếu muốn, nhưng không có câu văn nào mâu thuẫn với nó cả.

Số **tool** thì ngược lại: nó xuất hiện cả trong ảnh lẫn trong hai README, nên nó
được một phép thử canh giữ. `test_tool_contracts.py` đếm decorator `@mcp.tool`
bằng AST, đối chiếu với số tool server nạp được và với con số ghi trong cả hai
README; lệch một đơn vị là bộ test đỏ. Số hàng của bảng "khác biệt so với tài liệu
COM" cũng được canh như vậy — câu văn nói "16 khác biệt" phải khớp số hàng thật.

---

## Quy tắc khi thêm ảnh mới

1. **Lấy từ ứng dụng thật hoặc từ một lần chạy thật**, ở trạng thái do MCP server
   tạo ra.
2. **Ghi lại chuỗi lệnh** đã dùng, vào chính file này, đủ để người khác dựng lại.
3. **Chụp SAU CÙNG**, sau khi mọi thay đổi mã nguồn đã xong. Ảnh là bản sao duy
   nhất của một dữ kiện mà `grep` không sửa được và không test nào đọc được, nên
   chụp giữa chừng là tự tạo ra một bản sao lỗi thời — mà lại là bản người đọc
   tin nhất.
4. **Rà thông tin nhận diện trước khi commit** — tên tài khoản Autodesk ở góc trên
   phải, tab của các bản vẽ dự án đang mở, tên người dùng trong đường dẫn, toạ độ
   trắc địa trên thanh trạng thái, số bản quyền. Ảnh đã commit thì nằm vĩnh viễn
   trong lịch sử git; xoá file ở commit sau không gỡ được nó ra.
5. **Ảnh chụp sai cửa sổ trông y hệt ảnh chụp đúng.** Đây là rủi ro lớn nhất của
   cả quy trình, và nó đã xảy ra trong lúc dựng chính những ảnh trên:

   - *Chọn nhầm phiên.* Ứng dụng mở nhiều cửa sổ cùng tên tiến trình và cùng tiêu
     đề thì `-ProcessName` không tách được chúng. Nay nhiều ứng viên là **lỗi**,
     kèm danh sách PID; dùng `-ProcessId` để chỉ đích danh.
   - *Cửa sổ không lên được tiền cảnh.* Ảnh là bản copy vùng màn hình, nên khi
     `SetForegroundWindow` bị Windows từ chối — chuyện rất thường — script cũ vẫn
     chụp, và thu được ảnh của bất cứ cửa sổ nào đang nằm trên. Nay script kiểm
     chứng `GetForegroundWindow()` trước khi copy pixel, và dừng lại nếu sai.

   Điểm chung: ảnh vẫn đúng kích thước, vẫn không trống, nên mọi tín hiệu "thành
   công" đều đúng. **Hãy mở từng ảnh ra nhìn trước khi commit.**
6. **Che bằng cách tô đè, không làm mờ.** Làm mờ vẫn có thể đảo ngược một phần.
   Cả một dải giao diện cần biến mất thì cắt (`-Crop`) chứ đừng tô, vì một mảng
   trống lớn trông rõ là đã bị sửa.
