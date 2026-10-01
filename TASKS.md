# Nhiệm vụ Flow Kit

## 🔄 Nhiệm Vụ Đang Thực Hiện (In-Progress)

### AFF hybrid v1 độc lập

- Bắt đầu: 2026-10-01 09:44 (Asia/Bangkok), kế thừa audit offline trong AFF.
- [x] User duyệt implementation/fork/commit/push; Sol 6.1 và thứ tự gate.
- [x] Product/Ops khóa contract read-only: handoff, preview QA/hash trước promotion, không fake success.
- [x] Fork `hoaihoang218/flowkit`, branch `codex/flowkit-aff-hybrid-v1` đúng pin; giữ MIT.
- [x] Venv/dependency hẹp ngoài Git; không setup/pipeline upstream.
- [x] Backend: persistence/idempotency/auth/capability/handoff/receipt/probe/recovery.
- [x] Frontend standalone sau API lock; receipt, QA và đối soát cùng attempt dùng API thật.
- [x] QA độc lập: 58 Python +12 Node/browser PASS, đóng finding trong scope; chưa nghiệm thu live.
- [x] Commit code standalone `8e4b8b99706cb07461dfe8c4b705e65ac4fc9fa1` sau QA code pass.
- [x] Push standalone và đối chiếu SHA remote: `8e4b8b99706cb07461dfe8c4b705e65ac4fc9fa1` trên `origin/codex/flowkit-aff-hybrid-v1`.
- [ ] Nghiệm thu hai lane A1 với input/profile/project/ngân sách duyệt riêng và output thật.
- [ ] Sau gate mới tích hợp/pin adapter Studio và push branch đích.
- Files: `agent/`, `tests/hybrid/`, requirements, `protocol/`, `docs/`, `README.md`, `AGENTS.md`, records.
- Resume 10:46: Code `8e4b8b9` đã push, `git ls-remote` xác nhận SHA remote khớp; QA max độc lập 58 Python +12 Node/browser PASS, 0 skip; report `docs/hybrid-qa-2026-10-01.md`. Chưa live/nghiệm thu/Studio. User chưa chọn sản phẩm/bộ input/profile/project/budget riêng. Runtime ngoài Git, manifest inert chưa load. AFF chỉ routing/checkpoint, không API/UI/migration/production. Bước tiếp: chọn một sản phẩm/thư mục asset, kiểm tra bộ input rồi xin duyệt nghiệm thu riêng.

## ✅ Nhiệm Vụ Đã Thực Hiện (Completed)

### Bàn giao code standalone · 2026-10-01 10:46 (+07)

- Code và QA độc lập hoàn tất trong phạm vi standalone; đã commit/push `8e4b8b99706cb07461dfe8c4b705e65ac4fc9fa1` lên [branch fork](https://github.com/hoaihoang218/flowkit/tree/codex/flowkit-aff-hybrid-v1), đối chiếu SHA remote thành công.
- Files: `agent/hybrid/`, `agent/main.py`, `dashboard/hybrid/`, `extension/manifest.json`, `tests/hybrid/`, `tests/hybrid-ui/`, `protocol/`, requirements và tài liệu/records task-owned.
- Đây chỉ là hoàn thành phần code, không phải nghiệm thu vận hành hoặc tích hợp Studio. Task tổng vẫn ở In-Progress với gate live/Studio chưa đạt.
