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
- [ ] Commit/push standalone sau QA code pass.
- [ ] Nghiệm thu hai lane A1 với input/profile/project/ngân sách duyệt riêng và output thật.
- [ ] Sau gate mới tích hợp/pin adapter Studio và push branch đích.
- Files: `agent/`, `tests/hybrid/`, requirements, `protocol/`, `docs/`, `README.md`, `AGENTS.md`, records.
- Resume 10:38: QA max độc lập 58 Python +12 Node/browser PASS0skip, scope findings đã đóng; report `docs/hybrid-qa-2026-10-01.md`. Primary stage/commit/push code, chưa push tại checkpoint này. Chưa live/nghiệm thu/Studio. User chưa chọn sản phẩm/bộ input/profile/project/budget riêng. Runtime ngoài Git, manifest inert chưa load. AFF chỉ routing/checkpoint, không API/UI/migration/production.

## ✅ Nhiệm Vụ Đã Thực Hiện (Completed)

- Chưa nghiệm thu standalone; tạo fork/venv không phải hoàn thành sản phẩm.
