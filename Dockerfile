FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 7860
CMD ["sh", "-c", "BIND_HOST=0.0.0.0 PORT=${PORT:-7860} OTP_TOKEN=${OTP_TOKEN} python app.py"]
