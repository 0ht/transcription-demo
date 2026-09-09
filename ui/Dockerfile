# Docker Hub の匿名 pull レート制限回避のため Microsoft Container Registry ミラーを使用
FROM mcr.microsoft.com/devcontainers/python:3.11-bookworm

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

EXPOSE 8000

# FastAPI にはヘルス専用エンドポイントが無いためトップページへの疎通で代替
HEALTHCHECK CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/')" || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
