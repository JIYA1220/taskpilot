FROM python:3.12-slim
WORKDIR /app
COPY . .
# Editable install keeps the code at /app, where eval/directory.json is found.
RUN pip install --no-cache-dir -e .
EXPOSE 8000
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
