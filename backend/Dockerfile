FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV TORCHFREE_OCR_MODULE_PATH=/app/.easyocr-onnx

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN python -c "from ocr_service import prefetch_reader; prefetch_reader()"

CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}