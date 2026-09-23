ARG BUILD_FROM
FROM $BUILD_FROM

ARG BUILD_VERSION
ENV QSYS_QRC_VERSION=${BUILD_VERSION}

COPY requirements.txt /tmp/requirements.txt
RUN pip3 install --no-cache-dir -r /tmp/requirements.txt

COPY src /app/src
COPY static /app/static
# config.yaml travels with the image as the version fallback, because
# BUILD_VERSION has been seen not to reach the build.
COPY config.yaml /app/config.yaml

WORKDIR /app
CMD ["python3", "src/main.py"]
