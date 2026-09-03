package com.idv.backend;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.servlet.config.annotation.CorsRegistry;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;

/**
 * 개발 중에는 React 개발 서버(Vite)가 다른 포트에서 뜨므로 브라우저가 교차 출처로
 * 막는다. 허용 목록은 설정으로 빼 두어, 배포할 때 실제 도메인만 넣으면 되게 한다.
 */
@Configuration
public class WebConfig implements WebMvcConfigurer {

    private final String[] allowedOrigins;

    public WebConfig(@Value("${idv.cors.allowed-origins}") String origins) {
        this.allowedOrigins = origins.split("\\s*,\\s*");
    }

    @Override
    public void addCorsMappings(CorsRegistry registry) {
        // allowedOriginPatterns -- allowedOrigins()는 정확히 일치해야 해서 Cloudflare
        // Quick Tunnel처럼 매번 바뀌는 서브도메인(*.trycloudflare.com)을 못 받는다.
        registry.addMapping("/api/**")
                .allowedOriginPatterns(allowedOrigins)
                .allowedMethods("GET", "POST", "DELETE", "OPTIONS")
                .allowedHeaders("*");
    }
}
