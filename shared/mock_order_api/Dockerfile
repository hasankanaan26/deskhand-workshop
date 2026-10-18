# The mock order API in a container. Optional: uvicorn on your machine does the same.
#   docker build -t nimbus-mock shared/mock_order_api
#   docker run --rm -p 9000:9000 nimbus-mock
#   docker run --rm -p 9000:9000 -e MOCK_CHAOS=flaky nimbus-mock
FROM python:3.12-slim

WORKDIR /app

# Dependencies first, so editing app.py doesn't reinstall them.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py orders_seed.json ./

EXPOSE 9000
# 0.0.0.0, not localhost: inside a container, localhost is the container itself.
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "9000"]
