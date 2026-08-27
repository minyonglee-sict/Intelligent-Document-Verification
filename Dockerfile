# 엔진(api.py)과 워커(worker.py)가 같은 이미지를 쓴다 -- 둘 다 app/ 코드를 그대로
# 부르기만 하고, 실행 명령만 다르다(docker-compose.yml 의 command: 로 갈라진다).
#
# 베이스를 -bookworm 으로 못박은 이유: python:3.11-slim 의 기본 베이스가 최근
# Debian trixie(13)로 바뀌었는데, 마이크로소프트 ODBC 드라이버 저장소는 아직
# bookworm(12) 기준이라 그대로 쓰면 저장소 설정이 안 맞는다.
FROM python:3.11-slim-bookworm

# --- MS SQL Server ODBC 드라이버 설치 ---
# app/config.py 의 MSSQL_DRIVER 기본값이 "ODBC Driver 18 for SQL Server" 라, pyodbc가
# 이걸 찾는다. 마이크로소프트 공식 문서가 권장하는 .deb 패키지 방식을 쓴다 -- 저장소
# 목록과 서명 키를 손으로 sed로 끼워 맞추는 것보다 훨씬 덜 깨진다.
#
# libgl1/libglib2.0-0 도 같이 깐다: Docling이 문서를 읽을 때 쓰는 opencv-python이
# (실제로는 화면에 아무것도 안 그리는데도) X11/OpenGL 관련 공유 라이브러리를 찾는다
# -- slim 이미지엔 이게 아예 없어서 "libxcb.so.1: cannot open shared object file"
# 로 추출 자체가 실패했다.
RUN apt-get update && apt-get install -y --no-install-recommends curl libgl1 libglib2.0-0 \
    && curl -fsSL -O https://packages.microsoft.com/config/debian/12/packages-microsoft-prod.deb \
    && dpkg -i packages-microsoft-prod.deb \
    && rm packages-microsoft-prod.deb \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 unixodbc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# requirements 먼저 설치해야, 코드만 바뀌었을 때 이 레이어를 다시 안 태운다.
COPY requirements.txt .

# docling이 물고 오는 PyTorch는 그냥 설치하면 GPU(CUDA)용 빌드가 깔린다 --
# nvidia-cudnn/nvidia-nccl 등 부속 패키지까지 같이 받아서 이것만 2.7GB다. 이
# 컨테이너엔 GPU가 없고 CPU로만 돈다(Docling 자체가 CPU 추론). CPU 전용 휠을
# docling보다 먼저 깔아두면, 뒤이어 requirements.txt를 설치할 때 이미 만족된
# 요구사항으로 보고 CUDA판으로 갈아치우지 않는다.
RUN pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu

RUN pip install --no-cache-dir -r requirements.txt

COPY app/ app/
COPY *.py ./

# data/uploads, data/reports 는 docker-compose.yml 에서 호스트 볼륨으로 덮어씌운다.
RUN mkdir -p data/uploads data/reports

EXPOSE 8000

# 기본은 엔진. 워커는 docker-compose.yml 이 command 를 "python worker.py" 로 바꿔 쓴다.
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]
