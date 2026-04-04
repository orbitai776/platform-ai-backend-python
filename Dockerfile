FROM python:3.11-alpine

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Chỉ cần postgresql-client cho runtime
RUN apk add --no-cache postgresql-client

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 41002
CMD ["gunicorn", "core.wsgi:application", "--bind", "0.0.0.0:41002"]