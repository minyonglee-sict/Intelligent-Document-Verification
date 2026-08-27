package com.idv.backend;

import java.io.IOException;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Map;

import org.springframework.http.HttpMethod;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RequestPart;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.multipart.MultipartFile;

/**
 * 화면(React)이 부르는 API.
 *
 * <p>지금은 Python 엔진으로 넘기기만 한다. 인증·권한·감사 로그처럼 이 계층이 맡을
 * 것이 정해지면 각 메서드 앞에 붙는다. 그래서 경로를 하나로 뭉뚱그리지 않고
 * 엔드포인트마다 따로 두었다 -- 나중에 "승인은 검수자만" 같은 규칙을 걸 자리다.
 */
@RestController
@RequestMapping("/api")
public class DocumentController {

    private final EngineClient engine;

    public DocumentController(EngineClient engine) {
        this.engine = engine;
    }

    /** 엔진이 돌려준 상태 코드와 본문을 그대로 화면까지 전달한다. */
    private ResponseEntity<String> passThrough(ResponseEntity<String> upstream) {
        return ResponseEntity.status(upstream.getStatusCode())
                .contentType(MediaType.APPLICATION_JSON)
                .body(upstream.getBody());
    }

    @GetMapping("/health")
    public ResponseEntity<String> health() {
        return passThrough(engine.forward(HttpMethod.GET, "/health"));
    }

    // ------------------------------------------------------------------------
    // 로그인. 계정 검사·세션 토큰 발급/해제는 전부 엔진(api.py)이 한다 -- 여기는
    // 그대로 통로다. 로그인 뒤 다른 모든 요청에 실리는 Authorization 헤더는
    // EngineClient 의 요청 인터셉터가 자동으로 엔진까지 넘기므로, 그 요청들의
    // 메서드는 따로 손대지 않았다.
    // ------------------------------------------------------------------------

    @PostMapping(value = "/auth/login", consumes = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<String> login(@RequestBody String body) {
        return passThrough(engine.forward(HttpMethod.POST, "/auth/login", body));
    }

    @PostMapping(value = "/auth/signup", consumes = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<String> signup(@RequestBody String body) {
        return passThrough(engine.forward(HttpMethod.POST, "/auth/signup", body));
    }

    /** 로그인 화면에서 관리자에게 재설정을 요청할 때. 메일 발송 자체는 엔진(app/mailer.py)이 한다. */
    @PostMapping(value = "/auth/forgot-password", consumes = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<String> forgotPassword(@RequestBody String body) {
        return passThrough(engine.forward(HttpMethod.POST, "/auth/forgot-password", body));
    }

    @PostMapping("/auth/logout")
    public ResponseEntity<String> logout() {
        return passThrough(engine.forward(HttpMethod.POST, "/auth/logout"));
    }

    @GetMapping("/auth/me")
    public ResponseEntity<String> me() {
        return passThrough(engine.forward(HttpMethod.GET, "/auth/me"));
    }

    @PostMapping(value = "/auth/change-password", consumes = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<String> changePassword(@RequestBody String body) {
        return passThrough(engine.forward(HttpMethod.POST, "/auth/change-password", body));
    }

    // ------------------------------------------------------------------------
    // 사용자 관리(관리자 전용). role='admin' 검사는 전부 엔진이 한다 -- 여기는
    // 통로일 뿐이고, 관리자가 아니면 엔진이 403을 돌려준다.
    // ------------------------------------------------------------------------

    @GetMapping("/admin/users")
    public ResponseEntity<String> adminListUsers() {
        return passThrough(engine.forward(HttpMethod.GET, "/admin/users"));
    }

    @PostMapping(value = "/admin/users/{id}/role", consumes = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<String> adminSetRole(@PathVariable long id, @RequestBody String body) {
        return passThrough(engine.forward(HttpMethod.POST, "/admin/users/" + id + "/role", body));
    }

    @PostMapping(value = "/admin/users/{id}/reset-password", consumes = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<String> adminResetPassword(@PathVariable long id, @RequestBody String body) {
        return passThrough(engine.forward(HttpMethod.POST, "/admin/users/" + id + "/reset-password", body));
    }

    @GetMapping("/documents")
    public ResponseEntity<String> list(@RequestParam(required = false) String status) {
        String path = "/documents";
        if (status != null && !status.isBlank()) {
            path += "?status=" + URLEncoder.encode(status, StandardCharsets.UTF_8);
        }
        return passThrough(engine.forward(HttpMethod.GET, path));
    }

    @GetMapping("/documents/counts")
    public ResponseEntity<String> counts() {
        return passThrough(engine.forward(HttpMethod.GET, "/documents/counts"));
    }

    @GetMapping("/documents/{id}")
    public ResponseEntity<String> detail(@PathVariable long id) {
        return passThrough(engine.forward(HttpMethod.GET, "/documents/" + id));
    }

    @GetMapping("/documents/{id}/markdown")
    public ResponseEntity<String> markdown(@PathVariable long id) {
        return passThrough(engine.forward(HttpMethod.GET, "/documents/" + id + "/markdown"));
    }

    /** Docling 원시 출력. 수 MB 가 되기도 하므로 필요할 때만 부른다. */
    @GetMapping("/documents/{id}/docling-json")
    public ResponseEntity<String> doclingJson(@PathVariable long id) {
        return passThrough(engine.forward(HttpMethod.GET, "/documents/" + id + "/docling-json"));
    }

    /** 업로드는 접수만 하고 작업 번호를 돌려준다. 처리는 엔진에서 이어진다. */
    @PostMapping(value = "/documents", consumes = MediaType.MULTIPART_FORM_DATA_VALUE)
    public ResponseEntity<String> upload(
            @RequestParam("file") MultipartFile file,
            @RequestParam(name = "skipDuplicates", defaultValue = "true") boolean skipDuplicates)
            throws IOException {
        if (file.isEmpty()) {
            return ResponseEntity.badRequest()
                    .contentType(MediaType.APPLICATION_JSON)
                    .body("{\"detail\":\"빈 파일입니다.\"}");
        }
        String filename = file.getOriginalFilename();
        return passThrough(engine.upload(
                (filename == null || filename.isBlank()) ? "document.pdf" : filename,
                file.getBytes(),
                file.getContentType(),
                skipDuplicates));
    }

    @GetMapping("/jobs/{jobId}")
    public ResponseEntity<String> job(@PathVariable String jobId) {
        return passThrough(engine.forward(HttpMethod.GET, "/jobs/" + jobId));
    }

    @GetMapping("/jobs")
    public ResponseEntity<String> jobs() {
        return passThrough(engine.forward(HttpMethod.GET, "/jobs"));
    }

    /** 고친 값을 저장하고 규칙 검증을 다시 돌린다. 상태는 바뀌지 않는다. */
    @PostMapping(value = "/documents/{id}/recheck", consumes = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<String> recheck(@PathVariable long id, @RequestBody String body) {
        return passThrough(engine.forward(HttpMethod.POST, "/documents/" + id + "/recheck", body));
    }

    /**
     * VALIDATED 로 마감한다.
     *
     * <p>엔진은 critical 오류가 남아 있으면 409 를 돌려준다. 그 판단을 여기서 뒤집지
     * 않는다 -- 화면이 그 응답을 받아 사용자에게 한 번 더 묻는다.
     */
    @PostMapping(value = "/documents/{id}/approve", consumes = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<String> approve(@PathVariable long id, @RequestBody String body) {
        return passThrough(engine.forward(HttpMethod.POST, "/documents/" + id + "/approve", body));
    }

    /** 검증을 통과한 건들을 한 번에 마감한다. 엔진이 PENDING 인 것만 추린다. */
    @PostMapping(value = "/documents/bulk-approve", consumes = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<String> bulkApprove(@RequestBody String body) {
        return passThrough(engine.forward(HttpMethod.POST, "/documents/bulk-approve", body));
    }

    @DeleteMapping("/documents/{id}")
    public ResponseEntity<String> delete(@PathVariable long id) {
        return passThrough(engine.forward(HttpMethod.DELETE, "/documents/" + id));
    }

    /** 여러 건을 한 번에 지운다. 되돌릴 수 없으므로 화면이 먼저 확인받는다. */
    @PostMapping(value = "/documents/bulk-delete", consumes = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<String> bulkDelete(@RequestBody String body) {
        return passThrough(engine.forward(HttpMethod.POST, "/documents/bulk-delete", body));
    }

    // ----------------------------------------------------------------------
    // 화면에서 MCP 도구 쓰기. 실제 루프(LLM <-> MCP)는 엔진 쪽에 있다.
    // ----------------------------------------------------------------------

    /** 채팅이 쓸 수 있는 도구 목록. 연결 상태 확인에도 쓴다. */
    @GetMapping("/chat/tools")
    public ResponseEntity<String> chatTools() {
        return passThrough(engine.forward(HttpMethod.GET, "/chat/tools"));
    }

    /**
     * 질문 하나를 넘기고 답을 받는다.
     *
     * <p>LLM 이 도구를 여러 번 부를 수 있어 수십 초가 걸리기도 한다. 업로드처럼
     * 접수만 하고 끝낼 수도 있지만, 채팅은 답을 기다리는 것이 자연스러우므로
     * 그대로 붙들고 있는다 -- 대신 엔진 호출 제한시간을 넉넉히 잡아 두었다.
     */
    @PostMapping(value = "/chat", consumes = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<String> chat(@RequestBody String body) {
        return passThrough(engine.forward(HttpMethod.POST, "/chat", body));
    }

    // ----------------------------------------------------------------------
    // 오류 신고. 파일로 남으므로 DB 가 죽어 있어도 접수된다.
    // ----------------------------------------------------------------------

    @GetMapping("/reports")
    public ResponseEntity<String> reports(
            @RequestParam(defaultValue = "all") String scope) {
        return passThrough(engine.forward(
                HttpMethod.GET, "/reports?scope=" + URLEncoder.encode(scope, StandardCharsets.UTF_8)));
    }

    @GetMapping("/reports/counts")
    public ResponseEntity<String> reportCounts() {
        return passThrough(engine.forward(HttpMethod.GET, "/reports/counts"));
    }

    /** 캡처는 multipart 로, 붙여넣은 이미지는 data URL 문자열로 함께 온다. */
    @PostMapping(value = "/reports", consumes = MediaType.MULTIPART_FORM_DATA_VALUE)
    public ResponseEntity<String> createReport(
            @RequestPart(value = "files", required = false) List<MultipartFile> files,
            @RequestParam Map<String, String> fields,
            @RequestParam(value = "pasted", required = false) List<String> pasted)
            throws IOException {
        return passThrough(engine.createReport(fields, pasted, files));
    }

    @GetMapping("/reports/{slug}/images/{name}")
    public ResponseEntity<byte[]> reportImage(
            @PathVariable String slug, @PathVariable String name) {
        return engine.binary("/reports/" + enc(slug) + "/images/" + enc(name));
    }

    @PostMapping("/reports/{slug}/status")
    public ResponseEntity<String> reportStatus(
            @PathVariable String slug, @RequestParam String status) {
        return passThrough(engine.forward(
                HttpMethod.POST, "/reports/" + enc(slug) + "/status?status=" + enc(status)));
    }

    @DeleteMapping("/reports/{slug}")
    public ResponseEntity<String> deleteReport(@PathVariable String slug) {
        return passThrough(engine.forward(HttpMethod.DELETE, "/reports/" + enc(slug)));
    }

    private static String enc(String value) {
        return URLEncoder.encode(value, StandardCharsets.UTF_8).replace("+", "%20");
    }
}
