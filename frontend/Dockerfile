# 1단계 -- 빌드 (tsc -b && vite build)
FROM node:22-alpine AS build
WORKDIR /app

COPY package*.json ./
RUN npm ci

COPY . .
RUN npm run build

# 2단계 -- 정적 파일을 nginx로 서빙. /api 는 nginx.conf 가 Java 백엔드로 넘긴다.
FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
