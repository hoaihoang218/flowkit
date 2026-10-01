# Contract · AFF Flow Kit hybrid v1

Đây là contract fork, không phải cam kết capability upstream. Bản hiện tại chỉ điều phối handoff; không có transport tự động hợp lệ đã xác minh. Không bỏ After, thay V2V bằng I2V/R2V hoặc gọi endpoint browser nội bộ. MIT upstream giữ nguyên.

## Quyền và trạng thái

API chỉ bind loopback. Bearer local và session xác thực bảo vệ dữ liệu/mutation; Host/Origin kiểm tra exact. WebSocket chỉ phục vụ trạng thái, không phải kênh provider. Callback không cấp quyền hoàn tất khi transport chưa được bật. Bí mật/session không nằm trong URL, log hoặc Git.

Coarse `status` giữ tương thích; guard dựa trên `executionState`: `QUEUED`, `SUBMITTING`, `RUNNING`, `OUTPUT_RECEIVED`, `MANUAL_REQUIRED`, `OUTCOME_UNKNOWN`, `FAILED`, `CANCELED`. Receipt nhập xong chưa phải QA PASS hoặc hoàn thành vận hành. Không tự duyệt QA/P05.

Batch là nhóm row trong bảng `request` hiện có, không có queue thứ hai. Reservation bền vững trước handoff, một attempt, chạy tuần tự theo `order`. Double-click/reload trả trạng thái cũ; không thêm generation. Mất ACK/crash/expiry hoặc cancel sau dispatch không mở lại queue. Outcome chưa rõ phải đối soát; không có retry tự động.

## Request, authority và hash

Mở rộng `/api/requests/batch` bằng envelope có `idempotencyKey`, `payloadSha256`, batch/lane revision, thứ tự, action/specification và immutable authorization. Server tính lại RFC 8785/SHA-256 từ toàn envelope, loại đúng hai field `idempotencyKey` và `payloadSha256`; optional null được giữ, không tự loại. Same project/key/payload trả request cũ; khác payload `409`. Unique exact lane revision/action không cho key mới mở attempt thứ hai.

Authority khóa project/product/Content_ID/allocation/topic/format/Job/lane/Outfit/P02, input asset/hash/source window, profile/project provider và ngân sách. Standalone xác minh approval do operator xác thực đăng ký và input bytes local; chưa có Cloud authority adapter. Studio sau này phải kiểm tra state cloud tại admission/reservation/association, không tin snapshot local.

- `V2V_SOURCE_MOTION_BEFORE_AFTER_PERSON`: đúng ba role `SOURCE_MOTION`, `BEFORE`, `AFTER`; source chỉ cấp chuyển động, không đổi identity/outfit.
- `I2V_PRODUCT_ONLY`: đúng role `PRODUCT_IMAGE`; không nhận source-motion/face/outfit input.
- `preview-generation`: 720×1280, silent, chưa có parent QA.
- `1080-promotion`: 1080×1920 silent, exact preview SHA và immutable QA PASS receipt cùng authority.

Ngân sách dùng receipt và số generation/output, không dùng configured tier làm balance. Một provider request có thể tạo nhiều generation; tham chiếu [Google Flow Help](https://support.google.com/flow/answer/16526234?hl=en). Không hard-code giá từ bảng hiện tại. Zero cost chỉ khi được duyệt rõ, không chuyển unknown thành 0.

Golden fixture [hybrid-canonical.json](fixtures/hybrid-canonical.json) dùng synthetic data và chạy cả Python `rfc8785` và JavaScript `protocol/canonical-json.mjs`. Đây là kiểm tra protocol, chưa là test adapter Studio.

## Receipt và nghiệm thu

Không cần ZIP hoặc một file chứa toàn bộ binary. Có thể dùng thư mục/manifest đã có: lane person tham chiếu source-motion + Before + After, lane product-only tham chiếu ảnh sản phẩm; manifest giữ asset ID/path/SHA và approval. Sau khi operator chọn đúng bộ, Kit nhập bytes local để đối chiếu và giữ evidence ngoài Git. Tham chiếu manifest chưa là approval chạy provider.

Input/output nhập bằng upload bytes local; không fetch URL hoặc đọc path tùy ý. Server tính SHA và đo video duration/dimensions/FPS/audio. Manual receipt gắn exact request/reservation/attempt/fingerprint/session/actor cùng bằng chứng thao tác; operation/workflow ID có thể thiếu nếu Flow không cung cấp. Output sai scope/revision/hash không được association; output sau canceled/failed hoặc chưa đối soát quyền/chi phí chỉ làm evidence. Không tự nâng trạng thái provider/mode thành verified từ tệp import.

QA do reviewer xác thực nhập rõ verdict/checks cho đúng artifact SHA và authority. Preview QA PASS mới mở promotion, mỗi promotion có authorization riêng. Media metadata không thay input authority; chỉnh input tạo revision mới, không sửa receipt cũ.

Receipt có chi phí `UNKNOWN` được giữ evidence-only. Khi có evidence chi phí rõ, operator đối soát **cùng artifact và reservation cũ** bằng `outcome=OUTPUT_RECEIVED` kèm `clarification`: `artifactId`, `artifactSha256`, `receiptId`, `reservationId`, `observedGenerations`, `observedCostUnits`. Server yêu cầu cùng session/principal, CAS và đầy đủ authority/hash hiện tại, append association evidence; không sửa receipt cũ, không tạo attempt hoặc tự QA PASS. Mọi output index phải được association trước khi mở QA. Canceled/failed không được mở lại. Session hết hạn có thể renew đúng ID bằng bearer của operator; session mới không được tự chiếm reservation cũ.

Approval hết hạn khóa thao tác provider mới. Với output trả muộn, timestamp thao tác phải nằm trong cửa sổ approval/reservation đã duyệt; review/evidence muộn không phải quyền Generate thêm. Input hoặc preview bytes thay đổi chặn association/QA/promotion. Sau khi sửa material, phải dùng revision mới, không relabel lịch sử.

Nghiệm thu thật cần một batch A1 cùng sản phẩm: một lane person V2V + Before/After, một lane product-only I2V, mỗi lane preview 720p silent QA PASS rồi output thật 1080×1920 có lineage/receipt. Chưa có input/profile/project/budget duyệt riêng hoặc còn chờ output thì chưa hoàn tất. Test HTTP/WS/SQLite/fixture không thay gate này.

## Gate Studio và release

Chưa tích hợp Studio trước nghiệm thu standalone. Sau gate, AFF chỉ giữ adapter/pin commit; `google-flow` + `executorType=flowkit-local`, feature flag mặc định tắt, typed project namespace, reuse render_jobs/queue/events/artifacts/sessions và migration mới 0017. Legacy Google Flow endpoint giữ 410; không đổi womenswear/Vast.

Code commit/push, nghiệm thu standalone và Studio production là ba trạng thái riêng. Không deploy/migrate hoặc cleanup production trong đợt này.
