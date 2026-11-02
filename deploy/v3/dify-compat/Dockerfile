# 固定 Dify 版本和摘要，构建时校验并应用窄范围兼容补丁。
FROM langgenius/dify-api:1.18.0@sha256:0120970fb4ea59aa328d63b7dda635cccc071efb8aae470cd07115a026dbffd3
COPY --chown=dify:dify patch.py /tmp/v3-dify-compat.py
# 使用镜像自身的虚拟环境执行补丁，成功后移除临时脚本。
RUN /app/api/.venv/bin/python /tmp/v3-dify-compat.py && rm /tmp/v3-dify-compat.py
