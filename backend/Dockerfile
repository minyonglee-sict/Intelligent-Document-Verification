# 1단계 -- Maven 빌드 (mvnw로 wrapper가 지정한 Maven 버전을 그대로 씀)
FROM eclipse-temurin:21-jdk AS build
WORKDIR /app

COPY mvnw .
COPY .mvn .mvn
COPY pom.xml .
RUN chmod +x mvnw && ./mvnw -B dependency:go-offline

COPY src src
RUN ./mvnw -B clean package -DskipTests

# 2단계 -- 실행은 JDK가 아니라 JRE로 (이미지가 훨씬 작다)
FROM eclipse-temurin:21-jre
WORKDIR /app
COPY --from=build /app/target/backend-*.jar app.jar

EXPOSE 8080
ENTRYPOINT ["java", "-jar", "app.jar"]
