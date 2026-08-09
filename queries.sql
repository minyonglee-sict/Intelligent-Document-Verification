-- DocumentVerification (MS SQL Server) 조회용 쿼리 모음
--   접속: DBeaver > DocumentVerification (SQLEXPRESS)
--   서버: localhost\SQLEXPRESS   /   DB: DocumentVerification
--
-- DBeaver 사용법
--   가장 쉬운 순서: 접속을 먼저 고르고 편집기를 연다
--     1. 좌측 네비게이터에서 "DocumentVerification (SQLEXPRESS)" 를 클릭해 선택
--     2. 메뉴 SQL Editor > New SQL script
--        -> 그 접속에 연결된 편집기가 열린다. 여기에 아래 쿼리를 붙여넣어 쓴다
--
--   이 파일을 직접 열려면 (워크스페이스 밖이라 드래그하거나 File > Open File)
--     편집기 오른쪽 위 드롭다운 두 개에서
--       왼쪽  = 접속   -> DocumentVerification (SQLEXPRESS)
--       오른쪽 = 스키마 -> dbo
--     를 지정해야 실행된다. 안 하면 "No active connection" 이 뜬다.
--
--   실행: 쿼리 안에 커서를 두고 Ctrl+Enter (커서가 있는 한 문장만 실행)
--         Alt+X 는 파일 전체 실행이라 이 파일에서는 쓰지 말 것
--
-- markdown / docling_json 컬럼은 문서당 수십 KB라 SELECT * 는 피할 것.


-- ---------------------------------------------------------------------------
-- 0. 테이블 목록과 행 수 -- DBeaver로 만든 스키마가 제대로 있는지 먼저 확인
-- ---------------------------------------------------------------------------
-- 조인으로 세면 행수가 컬럼 수만큼 부풀려진다. 각각 따로 센다.
SELECT t.name AS 테이블,
       (SELECT SUM(p.rows) FROM sys.partitions p
         WHERE p.object_id = t.object_id AND p.index_id IN (0, 1)) AS 행수,
       (SELECT COUNT(*) FROM sys.columns c
         WHERE c.object_id = t.object_id)                          AS 컬럼수
FROM sys.tables t
ORDER BY t.name;


-- ---------------------------------------------------------------------------
-- 1. 테이블별 컬럼 정의 (SQLite 의 PRAGMA table_info 대응)
-- ---------------------------------------------------------------------------
SELECT t.name  AS 테이블,
       c.column_id AS 순서,
       c.name  AS 컬럼,
       ty.name AS 자료형,
       CASE WHEN ty.name LIKE 'n%char%' AND c.max_length > 0
            THEN c.max_length / 2 ELSE c.max_length END AS 길이,
       c.is_nullable  AS NULL허용,
       c.is_identity  AS 자동증가
FROM sys.columns c
JOIN sys.tables t ON t.object_id = c.object_id
JOIN sys.types ty ON ty.user_type_id = c.user_type_id
WHERE t.name IN ('documents', 'line_items', 'validation_errors')
ORDER BY t.name, c.column_id;


-- ---------------------------------------------------------------------------
-- 2. 문서 목록
-- ---------------------------------------------------------------------------
SELECT id,
       filename,
       status,
       page_count   AS 페이지,
       model        AS 모델,
       created_at   AS 등록,
       validated_at AS 승인
FROM documents
ORDER BY id DESC;


-- ---------------------------------------------------------------------------
-- 3. 상태별 집계
-- ---------------------------------------------------------------------------
SELECT status AS 상태, COUNT(*) AS 건수
FROM documents
GROUP BY status
ORDER BY 건수 DESC;


-- ---------------------------------------------------------------------------
-- 4. 추출 결과 요약 (머리말은 documents 의 컬럼이다)
-- ---------------------------------------------------------------------------
SELECT d.id,
       d.filename,
       d.invoice_number AS 송장번호,
       d.issue_date     AS 발행일,
       d.vendor_name    AS 공급자,
       d.buyer_name     AS 수신자,
       d.subtotal       AS 공급가액,
       d.tax            AS 세액,
       d.total_amount   AS 총액,
       (SELECT COUNT(*) FROM line_items i WHERE i.document_id = d.id) AS 품목수
FROM documents d
ORDER BY d.id DESC;


-- ---------------------------------------------------------------------------
-- 5. 품목 펼쳐보기
--    특정 문서만 보려면 WHERE d.id = 1 을 추가할 것.
-- ---------------------------------------------------------------------------
SELECT d.id,
       d.filename,
       i.position    AS 번호,
       i.description AS 품목,
       i.quantity    AS 수량,
       i.unit_price  AS 단가,
       i.amount      AS 금액
FROM line_items i
JOIN documents d ON d.id = i.document_id
ORDER BY d.id DESC, i.position;


-- ---------------------------------------------------------------------------
-- 6. 검산이 맞지 않는 품목 찾기 (규칙 엔진과 같은 계산을 SQL로)
-- ---------------------------------------------------------------------------
SELECT d.id,
       d.filename,
       i.position                AS 번호,
       i.description             AS 품목,
       i.quantity                AS 수량,
       i.unit_price              AS 단가,
       i.quantity * i.unit_price AS 계산값,
       i.amount                  AS 기재금액,
       ABS(i.quantity * i.unit_price - i.amount) AS 차이
FROM line_items i
JOIN documents d ON d.id = i.document_id
WHERE i.quantity   IS NOT NULL
  AND i.unit_price IS NOT NULL
  AND i.amount     IS NOT NULL
  AND ABS(i.quantity * i.unit_price - i.amount) > 0.02
ORDER BY d.id DESC, i.position;


-- ---------------------------------------------------------------------------
-- 7. 품목 합계 vs 기재 총액 대조
-- ---------------------------------------------------------------------------
SELECT d.id,
       d.filename,
       d.subtotal     AS 공급가액,
       d.tax          AS 세액,
       d.shipping     AS 배송비,
       d.total_amount AS 총액,
       SUM(i.amount)  AS 품목합계,
       d.total_amount - (ISNULL(d.subtotal, 0) + ISNULL(d.tax, 0) + ISNULL(d.shipping, 0)) AS 총액차이
FROM documents d
LEFT JOIN line_items i ON i.document_id = d.id
GROUP BY d.id, d.filename, d.subtotal, d.tax, d.shipping, d.total_amount
ORDER BY d.id DESC;


-- ---------------------------------------------------------------------------
-- 8. 미해결 검증 오류 (검수 화면에 빨간 뱃지로 뜨는 것들)
-- ---------------------------------------------------------------------------
SELECT d.id,
       d.filename,
       e.severity AS 심각도,
       e.source   AS 탐지,      -- rule = 규칙 엔진, llm = LLM
       e.field    AS 필드,
       e.message  AS 내용
FROM validation_errors e
JOIN documents d ON d.id = e.document_id
WHERE e.resolved = 0
ORDER BY d.id DESC,
         CASE e.severity WHEN 'critical' THEN 0 ELSE 1 END;


-- ---------------------------------------------------------------------------
-- 9. 오류 유형 통계 (규칙 엔진과 LLM 중 무엇이 무엇을 잡는지)
-- ---------------------------------------------------------------------------
SELECT source AS 탐지, severity AS 심각도, field AS 필드, COUNT(*) AS 건수
FROM validation_errors
GROUP BY source, severity, field
ORDER BY 건수 DESC;


-- ---------------------------------------------------------------------------
-- 10. 검수 이력 (승인된 건과 담당자 메모)
-- ---------------------------------------------------------------------------
SELECT d.id,
       d.filename,
       d.validated_at  AS 승인시각,
       d.reviewer_note AS 검수메모,
       (SELECT COUNT(*) FROM validation_errors e WHERE e.document_id = d.id) AS 총오류수
FROM documents d
WHERE d.status = 'VALIDATED'
ORDER BY d.validated_at DESC;


-- ---------------------------------------------------------------------------
-- 11. 거래처별 집계
-- ---------------------------------------------------------------------------
SELECT ISNULL(vendor_name, '(미확인)') AS 공급자,
       COUNT(*)                        AS 건수,
       SUM(total_amount)               AS 총액합계,
       MIN(issue_date)                 AS 최초발행일,
       MAX(issue_date)                 AS 최종발행일
FROM documents
WHERE status = 'VALIDATED'
GROUP BY vendor_name
ORDER BY 총액합계 DESC;


-- ---------------------------------------------------------------------------
-- 12. 처리 실패 건의 원인
-- ---------------------------------------------------------------------------
SELECT id, filename, created_at, failure_reason
FROM documents
WHERE status = 'FAILED'
ORDER BY id DESC;


-- ---------------------------------------------------------------------------
-- 13. 전체 초기화 -- 되돌릴 수 없다. 지울 때만 주석을 풀 것.
--     data/uploads 의 원본 PDF는 따로 지워야 한다.
-- ---------------------------------------------------------------------------
-- DELETE FROM line_items;
-- DELETE FROM validation_errors;
-- DELETE FROM documents;
-- DBCC CHECKIDENT ('line_items', RESEED, 0);
-- DBCC CHECKIDENT ('validation_errors', RESEED, 0);
-- DBCC CHECKIDENT ('documents', RESEED, 0);
