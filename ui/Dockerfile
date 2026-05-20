FROM python:3.11-slim

# 非 root ユーザーで実行（CIS Benchmark 推奨）
# UID 10001 は host UID と衝突しにくい高めの値を選択
RUN useradd --create-home --uid 10001 --shell /bin/bash appuser

WORKDIR /app

# 依存関係は root でインストール（/usr/local 配下に配置する必要があるため）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# アプリコードを appuser 所有でコピー
COPY --chown=appuser:appuser . .

# 以降は非 root で実行
USER appuser

EXPOSE 8501

HEALTHCHECK CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health')" || exit 1

CMD ["streamlit", "run", "app.py", \
     "--server.port=8501", \
     "--server.address=0.0.0.0", \
     "--server.headless=true", \
     "--server.maxUploadSize=1024", \
     "--server.maxMessageSize=1100", \
     "--browser.gatherUsageStats=false"]
