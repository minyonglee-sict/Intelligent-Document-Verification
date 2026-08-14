package com.idv.backend;

import java.time.Duration;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.core.io.ByteArrayResource;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpMethod;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.stereotype.Component;
import org.springframework.util.LinkedMultiValueMap;
import org.springframework.util.MultiValueMap;
import org.springframework.web.client.RestClient;

/**
 * Python 검증 엔진(api.py)을 부르는 통로.
 *
 * <p>이 백엔드는 문서 규칙을 스스로 갖지 않는다. 규칙을 두 벌로 만들면 화면과
 * 엔진의 판정이 갈라지고, 그때부터는 어느 쪽이 옳은지 아무도 모른다. 그래서 여기서는
 * 응답을 해석하지 않고 상태 코드와 본문을 그대로 넘긴다 -- 승인 거절(409)처럼
 * 엔진이 내리는 판단도 그대로 화면까지 도달해야 한다.
 *
 * <p>인증·권한·감사 로그처럼 이 계층에서 붙일 것이 생기면 {@link #forward} 앞뒤가
 * 그 자리가 된다.
 */
@Component
public class EngineClient {

    private final RestClient client;

    public EngineClient(
            @Value("${idv.engine.base-url}") String baseUrl,
            @Value("${idv.engine.timeout-seconds:120}") long timeoutSeconds) {
        SimpleClientHttpRequestFactory factory = new SimpleClientHttpRequestFactory();
        factory.setConnectTimeout(Duration.ofSeconds(10));
        // 업로드는 접수만 하고 끝나지만, 파일 전송 자체에 시간이 걸린다.
        factory.setReadTimeout(Duration.ofSeconds(timeoutSeconds));

        this.client = RestClient.builder()
                .baseUrl(baseUrl)
                .requestFactory(factory)
                .build();
    }

    /** 본문 없는 요청(GET/DELETE)을 그대로 넘긴다. */
    public ResponseEntity<String> forward(HttpMethod method, String path) {
        return forward(method, path, null);
    }

    /** JSON 본문을 그대로 넘긴다. body 가 null 이면 본문 없이 보낸다. */
    public ResponseEntity<String> forward(HttpMethod method, String path, String body) {
        RestClient.RequestBodySpec spec = client.method(method).uri(path);
        // body(...) 가 같은 객체를 돌려준다고 가정하지 않는다. 돌려받은 것을 쓴다.
        RestClient.RequestHeadersSpec<?> ready =
                (body == null) ? spec : spec.contentType(MediaType.APPLICATION_JSON).body(body);
        return ready.retrieve()
                // 4xx/5xx 도 예외로 만들지 않는다. 엔진의 판단(예: 승인 거절 409)을
                // 그대로 화면까지 전달해야 한다.
                .onStatus(status -> true, (req, res) -> { })
                .toEntity(String.class);
    }

    /** 업로드 파일을 multipart 로 엔진에 넘긴다. */
    public ResponseEntity<String> upload(String filename, byte[] data, String contentType) {
        ByteArrayResource resource = new ByteArrayResource(data) {
            @Override
            public String getFilename() {
                // 이름이 없으면 엔진이 확장자를 판별하지 못한다. Docling 은 확장자로
                // 형식을 가르므로 원본 이름을 그대로 지켜야 한다.
                return filename;
            }
        };

        HttpHeaders partHeaders = new HttpHeaders();
        if (contentType != null && !contentType.isBlank()) {
            partHeaders.setContentType(MediaType.parseMediaType(contentType));
        }
        HttpEntity<ByteArrayResource> filePart = new HttpEntity<>(resource, partHeaders);

        MultiValueMap<String, Object> form = new LinkedMultiValueMap<>();
        form.add("file", filePart);

        return client.post()
                .uri("/documents")
                .contentType(MediaType.MULTIPART_FORM_DATA)
                .body(form)
                .retrieve()
                .onStatus(status -> true, (req, res) -> { })
                .toEntity(String.class);
    }
}
