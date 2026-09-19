# Autodesk Civil 3D MCP Server

Máy chủ MCP điều khiển **Autodesk Civil 3D** đang chạy trên máy này qua COM, phục vụ
đề tài *Xây dựng mô hình thông tin hoàn công (As-built BIM) công trình hạ tầng giao
thông từ dữ liệu đám mây điểm*.

Nó là nửa sau của một cặp: [`recap-mcp`](https://github.com/xuantinhnbs-rgb/recap-mcp)
xử lý đám mây điểm (lọc, phân lớp, xuất điểm), còn máy chủ này nhận điểm đã lọc và
dựng **TIN surface → alignment → profile → corridor → trắc ngang → số liệu sai lệch**.
Hai máy chủ dùng được độc lập với nhau.

```
recap-mcp                     civil3d-mcp                          báo cáo
─────────                     ───────────                          ───────
LAS/LAZ ──lọc──> XYZ  ──>  TIN surface  ──┬─> volume surface ──> khối lượng đào/đắp
                                          ├─> alignment ─> profile ─> CSV lý trình–cao độ
                                          ├─> corridor ─> sample line ─> CSV trắc ngang
                                          └─> điểm kiểm tra ──> RMSE, độ lệch chuẩn
```

## Môi trường đã kiểm chứng

| Thành phần | Giá trị trên máy này |
|---|---|
| Civil 3D | 2026 (English), `C:\Program Files\Autodesk\AutoCAD 2026\` |
| Phiên bản COM | **13.8** (`AeccXUiLand.AeccApplication.13.8`) |
| Nền AutoCAD | `AutoCAD.Application.25.1` |
| Python | 3.14, cần `pywin32` và `mcp` |

Số hiệu COM **không** trùng năm phát hành. Máy chủ tự dò số đó từ registry chứ không
hardcode, nên vẫn chạy khi bạn nâng cấp Civil 3D — nhưng chữ ký API thì được lấy từ
type library của bản 13.8, nên bản khác cần thử lại.

## Cài đặt

```powershell
git clone https://github.com/xuantinhnbs-rgb/civil3d-mcp.git
cd civil3d-mcp
python -m pip install -r requirements.txt
```

Thêm vào `.mcp.json` của Claude Code (thay `<ĐƯỜNG_DẪN_REPO>` bằng nơi bạn vừa clone
về, và `<PYTHON>` bằng trình thông dịch Python 3.12+ trên máy bạn):

```json
{
  "mcpServers": {
    "civil3d-2026": {
      "command": "<PYTHON>",
      "args": ["<ĐƯỜNG_DẪN_REPO>/civil3d_server.py"],
      "cwd": "<ĐƯỜNG_DẪN_REPO>",
      "env": { "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8" }
    }
  }
}
```

`PYTHONIOENCODING` không phải tuỳ chọn trang trí: README và thông điệp lỗi của máy chủ
này viết bằng tiếng Việt, thiếu nó thì trên Windows tiến trình sẽ chết vì lỗi mã hoá
cp1252 chứ không phải vì logic sai.

## Kiến trúc

Civil 3D chạy **trên nền AutoCAD**: tiến trình vẫn là `acad.exe`, và API tự động hoá
có hai tầng chồng nhau.

| Tầng | Lấy bằng | Cho ra |
|---|---|---|
| AutoCAD | `GetActiveObject("AutoCAD.Application.25.1")` | ModelSpace, Layers, SendCommand, handle |
| Civil 3D – land | `acad.GetInterfaceObject("AeccXUiLand.AeccApplication.13.8")` | Surfaces, Alignments, Profiles, Points, Sites |
| Civil 3D – roadway | `acad.GetInterfaceObject("AeccXUiRoadway.AeccRoadwayApplication.13.8")` | Corridors, Assemblies, Subassemblies |

| File | Vai trò |
|---|---|
| `civil3d_mcp/com.py` | Luồng COM chuyên trách, dịch HRESULT, dò phiên bản từ registry, kết nối |
| `civil3d_mcp/client.py` | `Civil3DClient`: giữ kết nối, thử lại, truy cập tài liệu, đóng gói VARIANT |
| `civil3d_mcp/geometry.py` | Tính toán thuần Python: chia lô, dãy lý trình, thống kê RMSE, CSV |
| `civil3d_mcp/surfaces.py` | TIN surface, volume surface, nạp điểm, lấy mẫu, so sánh |
| `civil3d_mcp/alignments.py` | Tuyến, trắc dọc, profile view, quy đổi lý trình ↔ toạ độ |
| `civil3d_mcp/corridors.py` | Corridor, baseline, sample line, trắc ngang |
| `civil3d_mcp/research.py` | Sản phẩm cho báo cáo: lưới sai lệch, CSV, báo cáo Markdown |
| `civil3d_mcp/server.py` | 58 tool MCP |

### Ba quyết định thiết kế đáng chú ý

**Một luồng COM duy nhất.** Máy chủ MCP chạy tool trong thread pool, còn COM không
cho dùng con trỏ giao diện chéo luồng. Mọi lời gọi — kể cả hàm kiểm tra kết nối và các
property đọc tài liệu — đều đi qua `Civil3DClient.run()`. Bỏ sót một đường vào không
sinh lỗi tại chỗ; nó chỉ làm lỗi hiện ra muộn hơn ở một dòng chẳng liên quan.

**Thao tác ghi tự kiểm chứng.** Một lời gọi COM không ném lỗi *không* phải bằng chứng
thao tác đã có hiệu lực. Mọi tool ghi đều đọc lại kết quả và trả về `verified` +
`verified_by`. Ví dụ `add_points_to_surface` báo `points_added_measured` lấy từ chênh
lệch `Statistics.NumberOfPoints` trước/sau, chứ không phải số điểm đã gửi.

**Lô đầu tiên là phép hiệu chuẩn.** `AddPointMultiple` nhận một SAFEARRAY mà tài liệu
không nói rõ bố cục, và bố cục sai thì nó im lặng không thêm điểm nào. Vì vậy lô đầu
được gửi theo bố cục phẳng rồi đo lại; số điểm không tăng thì tự chuyển sang bố cục
mảng-của-mảng. Khoá `array_layout_used` trong kết quả cho biết bố cục nào đã ăn.

## Bộ tool (58 tool)

### Kết nối và bản vẽ
| Tool | Việc |
|---|---|
| `check_civil3d_connection` | **Gọi đầu tiên.** Phân biệt: chưa cài / chưa chạy / đang bám nhầm AutoCAD thuần |
| `launch_civil3d` | Khởi động với đúng tham số `/ld AecBase.dbx /p <<C3D_Metric>> /product C3D` |
| `create_new_drawing` | Bản vẽ mới từ template — cách đúng để bắt đầu (mở thẳng .dwt là sửa template của máy) |
| `get_drawing_info`, `list_open_drawings`, `open_drawing`, `save_drawing`, `switch_drawing` | Quản lý bản vẽ |
| `send_civil3d_command` | Lối thoát cho chức năng COM không có. **Bất đồng bộ, không trả kết quả** |
| `get_environment_report` | Bản ghi phiên bản phần mềm cho mục "môi trường thực nghiệm" |

### Bề mặt
| Tool | Việc |
|---|---|
| `list_surfaces`, `get_surface_info` | Liệt kê, thống kê, hộp bao, thành phần định nghĩa |
| `create_tin_surface` | TIN surface rỗng |
| `add_points_to_surface` | Nạp điểm qua COM — dùng cho vài nghìn điểm |
| `add_point_file_to_surface` | Gắn file điểm trên đĩa — **dùng cho dữ liệu quét thật** |
| `build_surface_from_xyz_file` | Một bước: tạo + nạp + đặt `max_triangle_length` + rebuild |
| `import_surface_file` | LandXML / TIN / DEM |
| `set_surface_build_options`, `rebuild_surface`, `delete_surface` | Tham số dựng TIN, rebuild, xoá (cần `confirm=True`) |
| `create_volume_surface` | So hai bề mặt → khối lượng đào/đắp/thực |
| `sample_surface_elevations`, `sample_surface_section` | Cao độ tại điểm; mặt cắt theo đoạn thẳng |
| `compare_surface_to_check_points` | RMSE so với điểm kiểm tra ngoại nghiệp |
| `compare_two_surfaces` | Lưới sai lệch cao độ trong vùng chồng lấn |

### Tuyến và trắc dọc
| Tool | Việc |
|---|---|
| `list_alignments`, `get_alignment_info` | Quét cả tuyến siteless và tuyến trong Site |
| `create_alignment`, `create_alignment_from_polyline` | Tạo tuyến từ dãy đỉnh hoặc từ polyline |
| `alignment_station_offset`, `alignment_point_location`, `sample_alignment` | Quy đổi lý trình ↔ toạ độ |
| `list_profiles`, `create_profile_from_surface`, `sample_profile` | Trắc dọc từ bề mặt, bảng lý trình–cao độ–độ dốc |
| `compare_profiles` | Sai lệch cao độ hoàn công vs thiết kế theo lý trình |
| `create_profile_view` | Khung nhìn trắc dọc |

### Corridor và trắc ngang
| Tool | Việc |
|---|---|
| `list_assemblies`, `list_corridors`, `get_corridor_info` | Liệt kê, baseline, vùng lý trình, mặt corridor |
| `list_assembly_library`, `import_assembly` | **Cách tạo assembly khi chỉ có COM**: chép từ thư viện assembly mẫu của Civil 3D |
| `create_corridor`, `add_corridor_baseline`, `rebuild_corridor` | Dựng và tính corridor |
| `sample_corridor_surface` | Cao độ mặt corridor |
| `corridor_points`, `corridor_shape_areas` | Hình học corridor đã tính: điểm kèm mã, diện tích từng lớp kết cấu |
| `create_sample_lines`, `create_sections`, `read_sections` | Sample line và trắc ngang |

### Xuất số liệu
| Tool | Việc |
|---|---|
| `export_surface_comparison` | CSV lưới + CSV thống kê + báo cáo Markdown ghi đủ điều kiện sinh số liệu |
| `export_profile_csv`, `export_sections_csv` | Trắc dọc, trắc ngang ra CSV |
| `export_check_point_report` | Đối chiếu điểm kiểm tra, xuất CSV kèm RMSE |
| `export_corridor_points_csv` | Điểm hình học corridor ra CSV |
| `export_corridor_quantities` | Diện tích theo lý trình + thể tích từng lớp kết cấu |

## Giới hạn đã biết

Những điều này là **tính chất của COM Civil 3D**, không phải thiếu sót có thể vá trong
máy chủ này:

1. **Không tạo được assembly và subassembly *từ đầu*.** `AeccAssemblies` chỉ có `Count`
   và `Item` — đã kiểm bằng type library, không phải suy đoán. Nhưng **chép được**:
   `import_assembly` chèn một bản vẽ chứa assembly vào bản vẽ hiện hành và assembly đi
   theo. Civil 3D cài sẵn 16 bản vẽ assembly mẫu ở
   `C:\ProgramData\Autodesk\C3D 2026\enu\Assemblies\Metric\` (xem `list_assembly_library`),
   nên trong thực tế giới hạn này không còn chặn đường nữa.
2. **Không gán được target cho corridor.** `AeccBaselineRegion` không có thành viên nào
   về target, nên subassembly cần bề mặt đích (mọi loại daylight / side slope) sẽ làm
   `Rebuild` hỏng với `0x80004005`. Hai cách đi tiếp: chọn assembly không có subassembly
   daylight, hoặc xoá subassembly đó sau khi nhập (`subassembly.Delete()` qua COM được).
3. **Không tạo được corridor surface.** `AeccCorridorSurfaces` cũng chỉ đọc. Thay vào đó
   dùng `corridor_points` / `corridor_shape_areas`: chúng đọc thẳng hình học corridor đã
   tính, đủ cho bảng lý trình–offset–cao độ và cho khối lượng theo lớp.
4. **Không sửa được tham số subassembly.** `ParamsDouble`/`ParamsLong`/… trả về đối tượng
   COM không gọi được `Count` lẫn `Item`, và `CastTo` với type library đã makepy vẫn báo
   "Element not found". Bề rộng làn, độ dốc ngang… phải sửa trong bảng thuộc tính của
   Civil 3D.
5. **Lệnh gửi qua `send_civil3d_command` là một chiều.** COM trả về khi lệnh được xếp
   hàng, không có mã lỗi nào quay lại, và lệnh nào chờ người dùng bấm chuột sẽ treo
   giao diện. Sau mỗi lệnh phải kiểm chứng bằng một tool đọc.
6. **Bản vẽ phải dựng từ template Civil 3D.** Tạo surface/alignment/profile đều đòi một
   style có thật; bản vẽ AutoCAD trắng không có style nào và tool sẽ báo lỗi rõ.
7. **Không xuất được LandXML qua COM** (chỉ nhập được). Muốn xuất thì phải qua
   `send_civil3d_command` với lệnh `-LandXMLOut`, và kiểm chứng bằng cách kiểm tra file
   trên đĩa.
8. **Corridor lớn rebuild rất lâu** và COM chặn cho tới khi xong. Đọc khoá `out_of_date`
   trong kết quả: còn `true` nghĩa là chưa tính xong.
9. **Đơn vị đi theo bản vẽ.** Máy chủ không tự quy đổi. `get_drawing_info` trả về
   `measurement` và `drawing_units` để kiểm tra trước khi đọc số liệu.
10. **Dấu của khối lượng theo quy ước Civil 3D:** cut là phần bề mặt so sánh *thấp hơn*
   bề mặt gốc. Kết quả của `create_volume_surface` ghi kèm quy ước này.

## Quy trình mẫu cho đề tài

```
1. check_civil3d_connection            → xác nhận bám đúng Civil 3D 2026
2. open_drawing(template có style)     → hoặc bản vẽ thiết kế đã có
3. build_surface_from_xyz_file(
     "HOANCONG", "duong_da_loc.xyz",
     max_triangle_length=5)            → TIN hoàn công từ đám mây điểm đã lọc
4. import_surface_file("thietke.xml")  → TIN thiết kế từ LandXML
5. export_surface_comparison(
     "HOANCONG", "THIETKE", out_dir,
     spacing=1, edge_inset=2,
     volume_surface_name="SO_SANH")    → CSV + thống kê + khối lượng + báo cáo
6. export_check_point_report(
     "HOANCONG", "diem_kiem_tra.csv")  → RMSE so với số đo độc lập
7. create_alignment_from_polyline(...)  → tim tuyến
8. create_profile_from_surface(...)     → trắc dọc hoàn công
9. compare_profiles(...)                → sai lệch cao độ theo lý trình
10. create_sample_lines + create_sections + export_sections_csv → trắc ngang
```

Bước 6 là bước có giá trị nhất khi bảo vệ kết quả: điểm kiểm tra không tham gia dựng
bề mặt nên sai lệch tính ra là số đo độc lập. Bước 5 cho hai phép đo khối lượng độc
lập nhau (lưới lấy mẫu và TIN của Civil 3D); chênh lệch giữa chúng chính là ảnh hưởng
của bước lưới, và nên được báo cáo chứ không nên làm tròn cho khớp.

## Khác biệt so với tài liệu COM

Mười hai điểm dưới đây **đo được trên Civil 3D 2026 (COM 13.8)**, không suy đoán: mỗi
điểm là một lời gọi bị từ chối hoặc trả kết quả rỗng cho tới khi làm đúng cách bên
cột phải. Chúng đã được sửa trong máy chủ này; liệt kê ra để ai đọc code không nghĩ
các dòng đó là thừa, và để người viết công cụ Civil 3D khác đỡ mất thời gian.

| # | Lời gọi | Tài liệu / trực giác nói | Thực tế đo được |
|---|---|---|---|
| 1 | `Surfaces.AddTinSurface` | Chỉ cần `Name` + `Style` | Đòi **cả `Layer` lẫn `BaseLayer`**; thiếu `BaseLayer` → `E_INVALIDARG` hiện ra là "Exception occurred" |
| 2 | `Surfaces.AddTinVolumeSurface` | `Description` là tuỳ chọn | **`Description` rỗng bị từ chối**; 4 tổ hợp mô tả rỗng đều hỏng, tổ hợp có mô tả thì chạy. `AddTinSurface` thường lại không đòi |
| 3 | `Profiles.AddFromSurface` | Tham số `Surface` nhận đối tượng bề mặt | Đòi **TÊN bề mặt dạng chuỗi**; truyền đối tượng → `E_INVALIDARG` |
| 4 | `SampleLineGroups.Add`, `SampledSurfaces.AddAllSurfaces` | Đối xứng với (3), tức nhận tên | Ngược lại: đòi **ĐỐI TƯỢNG style**; truyền tên → `TypeError` của pywin32 |
| 5 | `SampledSurfaces.AddAllSurfaces` | Thêm bề mặt là xong | Thêm nhưng để **cờ `Sample` TẮT**, nên `CreateSectionsAtSampleLines` chạy trót lọt mà **không sinh section nào** |
| 6 | `AlignmentEntities.AddFixedLine1` | Toạ độ 2D là đủ cho hình học bằng | Đòi **mảng 3 thành phần**; mảng 2 thành phần → `E_INVALIDARG` |
| 7 | `AeccSurfaceType`, `AeccProfileType`, `AeccAlignmentEntityType` | Enum đánh số từ 0 | Đánh số **từ 1**. Truyền 0 bị từ chối bằng `E_INVALIDARG`, còn khi chỉ dùng để hiển thị thì mọi TIN surface bị gán nhãn sai thành volume surface |
| 8 | `Document.ProfileStyles` | Style trắc dọc của Civil 3D | **Luôn rỗng** (đó là collection của tầng AEC nền). Style trắc dọc nằm ở **`LandProfileStyles`** |
| 9 | `ProfileViews.Add` | Chuỗi rỗng ở tham số bộ band = "không cần band" | **Đòi tên một bộ band CÓ THẬT**; chuỗi rỗng → `E_INVALIDARG`. Nghĩa "không có band" là một thành viên có tên: **`_No Bands`**. Và collection chứa chúng tên là **`ProfileViewBandStyleSets`**, không phải `ProfileViewBandSetStyles`; `ProfileViewBandStyles` có tồn tại nhưng không đọc được `Count` |
| 10 | `AeccRoadwayDocument.Assemblies` | Bản vẽ chưa có assembly → collection rỗng | **Chính property bị từ chối** bằng `E_INVALIDARG` khi rỗng, nên "chưa có assembly" trông y hệt "lời gọi hỏng". `Corridors` cùng đối tượng cha vẫn trả về 0 bình thường, nên không suy rộng được |
| 11 | `Corridors.Add` | Corridor có ngay sau khi Add | **Chưa thấy trong collection** ở lần đọc đầu; phải thử lại vài trăm ms. Kiểm chứng đọc quá sớm báo hỏng một thao tác đã thành công, và người dùng tạo lại thì sinh corridor thứ hai |
| 12 | `ModelSpace.InsertBlock` với .dwg chứa đối tượng AEC | Đối tượng nằm trong định nghĩa block, phải `Explode` mới ra | Civil 3D **trộn thẳng** đối tượng AEC vào cơ sở dữ liệu ngay khi chèn; `Explode` thêm sinh ra **bản sao thứ hai** (đo được: 2 assembly + 6 subassembly thay vì 1 + 3) |

Ba điểm nữa về hành vi, không phải tham số:

- **`Alignment.Entities.Count` còn là 0 ngay sau khi thêm đoạn**, trong khi `Length`
  đã đúng. Một phép kiểm chứng đọc quá sớm còn tệ hơn không kiểm chứng: nó báo hỏng
  một thao tác đã thành công. Máy chủ vì vậy kiểm chứng tuyến bằng chiều dài.
- **Civil 3D đang bận từ chối `GetActiveObject` cho mọi ProgID**, y như khi chưa
  chạy. Không phân biệt hai trường hợp này thì người dùng được khuyên mở thêm một
  phiên thứ hai — đúng việc không nên làm. `check_civil3d_connection` trả về khoá
  `busy` riêng.
- **pywin32 phát `AttributeError` cho cả "đang bận" lẫn "thuộc tính không tồn tại"**,
  với thông điệp giống nhau. Phép phân biệt đáng tin duy nhất là thời gian: chờ một
  nhịp rồi đọc lại (`com.read_optional`).

## Kiểm thử

```powershell
python -m pytest tests -q                    # 86 phép thử, không cần Civil 3D
$env:CIVIL3D_LIVE_TEST=1; python -m pytest tests/test_live_civil3d.py -q   # 13 phép thử trên Civil 3D thật
```

**86 phép thử offline** chạy được trên máy không có Civil 3D. Bộ này dùng một lớp COM
giả để đi qua đúng những đường xử lý mà máy thật ít khi chạm tới:

- lời gọi COM "thành công" nhưng không có hiệu lực (tạo bề mặt không ra bề mặt, nạp
  điểm không vào điểm nào);
- bố cục mảng sai bị im lặng bỏ qua, và phép hiệu chuẩn phải phát hiện được;
- hồi quy cho các khác biệt #1, #2, #6 ở bảng trên — bỏ dòng gán `BaseLayer` hoặc
  chuyển toạ độ về 2D sẽ làm phép thử đỏ;
- hai bề mặt không chồng lấn (dấu hiệu lệch hệ toạ độ) phải báo lỗi nói rõ nguyên nhân;
- "đang bận" phải phân biệt được với "chưa chạy";
- thống kê sai lệch: RMSE quanh 0 khác độ lệch chuẩn quanh trung bình khi có bias.

Phần tính toán trong `geometry.py` được kiểm thử bằng giá trị tính tay được (tam giác
3-4-5, RMSE của dãy đối xứng, diện tích hình vuông), không dùng giá trị tham chiếu
chép lại từ một lần chạy trước.

**13 phép thử live** (`CIVIL3D_LIVE_TEST=1`) dựng hai mặt phẳng song song cách nhau
đúng 0,05 m trên diện 100 × 40 m, rồi đòi Civil 3D trả về đúng đáp số giải tích:

| Đại lượng | Đáp số tính tay | Civil 3D trả về |
|---|---|---|
| RMSE chênh cao độ so điểm kiểm tra | 0,050 m | 0,050 m |
| Độ lệch chuẩn (chênh không đổi) | 0 | 0 |
| Khối lượng đắp | 100 × 40 × 0,05 = 200 m³ | 200,000 m³ |
| Độ dốc dọc trên mặt cắt | 0,020 | 0,020 |
| Cao độ trắc dọc tại Km0+000 / +100 | 10,00 / 12,00 m | 10,00 / 12,00 m |

"Chạy không lỗi" không phải tiêu chí: mọi bước phải ra đúng con số. Chính bộ thử này
đã phát hiện toàn bộ tám khác biệt ở bảng trên.
