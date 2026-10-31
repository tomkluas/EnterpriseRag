FROM langgenius/dify-api:1.18.0@sha256:0120970fb4ea59aa328d63b7dda635cccc071efb8aae470cd07115a026dbffd3
COPY --chown=dify:dify patch.py /tmp/v3-dify-compat.py
RUN /app/api/.venv/bin/python /tmp/v3-dify-compat.py && rm /tmp/v3-dify-compat.py
