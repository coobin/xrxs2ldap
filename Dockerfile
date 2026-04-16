FROM python:3.12-slim

ENV TZ=Asia/Shanghai
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY samples ./samples

RUN pip install --no-cache-dir .

CMD ["xrxs2ldap"]
