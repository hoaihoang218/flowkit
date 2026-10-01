# Flow Kit · AFF hybrid v1

Fork riêng cho AFF: điều phối batch đã duyệt, lưu authority/receipt và nhận media thật; không mặc định tự động hóa Google Flow.

Nguồn: [crisng95/flowkit tại pin fe40decd](https://github.com/crisng95/flowkit/tree/fe40decdcc447fbfc84fcf0e522a2f0e302d2c28). Giữ [MIT](LICENSE) và copyright upstream. Branch `codex/flowkit-aff-hybrid-v1`.

## Khác biệt với upstream

- Entry point an toàn, không khởi động worker/SDK/FlowClient upstream.
- Chưa có transport tự động hợp lệ đã xác minh. V2V, I2V và upscale đều `MANUAL_REQUIRED`: người dùng thao tác Flow rồi nhập receipt/output. Không bypass, fake telemetry hoặc cookie browser. Framework telemetry tắt.
- API loopback, bearer/session và Host/Origin exact; WS chỉ trạng thái. Không generate RPC cũ/extension transport.
- Giữ `/api/requests/batch` và bảng `request`; SQLite atomic, fingerprint/idempotency, một attempt, tuần tự, không retry. Unknown/cancel/crash không mở generation mới.
- Upload bytes local, tính SHA và đo dimensions/duration/FPS/audio. Preview QA PASS exact hash mới mở promotion 1080p; không tự duyệt QA/P05.
- DB/output/secret/venv ở `%LOCALAPPDATA%\AFF\flowkit`, ngoài Git; không đưa prompt sản xuất/asset/credential lên fork công khai.

Module/skill/extension upstream giữ trong nguồn nhưng không thuộc runtime hybrid. Không chạy `setup.sh`, `setup.py`, start script/pipeline upstream hoặc load unpacked extension. Lời “live-verified” upstream không là bằng chứng nghiệm thu fork.

## Chạy local khi người dùng sẵn sàng

Python 3.12 trên Windows; chuẩn bị môi trường riêng:

```powershell
python -m venv "$env:LOCALAPPDATA\AFF\flowkit\venv"
& "$env:LOCALAPPDATA\AFF\flowkit\venv\Scripts\python.exe" -m pip install -r requirements-hybrid.txt
```

Tại thư mục fork, người dùng chủ động chạy:

```powershell
& "$env:LOCALAPPDATA\AFF\flowkit\venv\Scripts\python.exe" -m uvicorn agent.main:app --host 127.0.0.1 --port 8100 --no-access-log
```

Mở bàn thao tác ở `http://127.0.0.1:8100/hybrid/`. Không `--reload`, không bind `0.0.0.0`. Token local được provision tại `%LOCALAPPDATA%\AFF\flowkit\local-token.txt`; không gửi token trong chat/URL/Git. Không có service/watcher thường trực do Codex cài. Luồng/API: [contract hybrid](docs/hybrid-v1.md).

Trước Generate/upscale phải duyệt riêng exact input/hash, prompt/mode/model, profile/project và ngân sách. Approval implementation không thay approval sản xuất. Operation ID có thể thiếu nếu Flow không cung cấp; không bịa ID.

## Kiểm tra hẹp

```powershell
& "$env:LOCALAPPDATA\AFF\flowkit\venv\Scripts\python.exe" -m pip install -r requirements-hybrid-test.txt
& "$env:LOCALAPPDATA\AFF\flowkit\venv\Scripts\python.exe" -m pytest tests/hybrid -q
node --test protocol/canonical-json.test.mjs tests/hybrid-ui/client.test.mjs tests/hybrid-ui/browser.test.mjs
```

SQLite/HTTP/WS/media fixture local, không provider. Không full test/build/dev server/watch. Synthetic media không chứng minh generation hoặc fidelity của Flow.

Browser test dùng Playwright đã có sẵn và browser đã cài. Có thể đặt `FLOWKIT_PLAYWRIGHT_MODULE` trỏ module local và `FLOWKIT_BROWSER_EXECUTABLE` trỏ executable; nếu thiếu dependency/browser test sẽ ghi `SKIP` rõ ràng, không tự cài. `SKIP` không là bằng chứng UI đã được kiểm tra.

## Trạng thái bàn giao

- Code đã qua QA hẹp độc lập: 58 Python và 12 Node/browser test PASS. Bản commit/push được ghi trong handoff; không đồng nhất với nghiệm thu vận hành.
- Standalone chưa nghiệm thu vận hành: cần batch A1 hai lane cùng sản phẩm (person V2V/source-motion-only + Before/After, product-only I2V), preview 720×1280 silent QA PASS rồi output thật 1080×1920 có lineage.
- Studio chưa tích hợp adapter vì gate standalone chưa đạt; production chưa deploy/migrate. Topic/Content_ID/Outfit Lock đã có ở AFF, không làm lại trong fork.

Handoff chờ output không là thành công. Sau nghiệm thu AFF adapter pin đúng fork commit đã QA, không copy cả fork vào Studio.
