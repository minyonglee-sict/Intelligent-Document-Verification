package com.idv.backend;

import java.io.IOException;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;

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

    /** 업로드는 접수만 하고 작업 번호를 돌려준다. 처리는 엔진에서 이어진다. */
    @PostMapping(value = "/documents", consumes = MediaType.MULTIPART_FORM_DATA_VALUE)
    public ResponseEntity<String> upload(@RequestParam("file") MultipartFile file)
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
                file.getContentType()));
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

    @DeleteMapping("/documents/{id}")
    public ResponseEntity<String> delete(@PathVariable long id) {
        return passThrough(engine.forward(HttpMethod.DELETE, "/documents/" + id));
    }
}
