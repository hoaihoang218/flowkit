# Flow Kit · AFF hybrid v1

## Phạm vi hiện hành

- Fork từ upstream `fe40decdcc447fbfc84fcf0e522a2f0e302d2c28`; giữ MIT và copyright upstream. Branch `codex/flowkit-aff-hybrid-v1`.
- Contract hiện hành là [hybrid-v1](docs/hybrid-v1.md), không phải pipeline/skill upstream. Không chạy `setup.sh`, `setup.py`, pipeline hoặc extension upstream.
- Không dùng nhánh né CAPTCHA/phát hiện extension, telemetry giả, cookie hoặc credential browser. Chưa có transport hợp lệ đã xác minh: mọi thao tác provider cần người dùng handoff.
- Giữ queue/bảng `request` và `/api/requests/batch`; không tạo queue song song. Một batch đã duyệt, tuần tự, không retry hay tự thêm attempt. Outcome chưa rõ phải đối soát.
- Runtime SQLite/output/secret/venv ở `%LOCALAPPDATA%/AFF/flowkit`, ngoài repo. Không commit asset, prompt sản xuất, secret, DB hoặc output. Field API/slug giữ ASCII vì contract kỹ thuật; nội dung tiếng Việt có dấu Unicode.
- Ghi prompt tại `.agent/history/YYYY-MM/prompts.md`; đồng bộ `TASKS.md` và `.agent/history/CURRENT_TASK.md`, không ghi đè task khác.

## Ownership và model

- Tất cả vai trò `gpt-6.1-sol`: Product/Ops high read-only, Backend xhigh, Frontend high, QA max read-only, tích hợp max. `light=low`; `ultra` chỉ khi tranh luận an toàn/correctness. Không Antigravity/model khác.
- Tối đa ba subagent đồng thời, một writer/file. Backend sở hữu `agent/`, `tests/hybrid/`, `requirements-hybrid*.txt`; Frontend nhận UI sau API lock; agent chính docs/canonical JS/golden fixtures/records/Git.
- Product → Backend standalone → QA → nghiệm thu standalone → Backend Studio → Frontend → QA tích hợp → commit/push. Test local không thay nghiệm thu hai lane thật.
- Không dev server/watch/full test/build; chỉ test hẹp HTTP/WS/SQLite/media fixture. Không provider network, extension install, Generate/canary khi chưa duyệt exact input/profile/project/budget.
- Agent chính stage/commit/push sau QA; không reset/stash/clean/ghi đè dirty changes. Không deploy/migrate Cloudflare/cleanup production/publish.

## Handoff

- Person-motion giữ V2V/source-motion-only, Before/After; product-only I2V, không face/outfit hoặc fallback.
- Preview bytes thật 720×1280 silent → QA PASS exact hash → promotion riêng và artifact thật 1080×1920.
- Không tự duyệt QA/P05. Báo riêng: code push, standalone nghiệm thu, Studio production chưa triển khai.
