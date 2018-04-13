# WARNING: Stackdriver Debugger is not regularly tested on the Alpine Linux
# platform and support will be on a best effort basis.
# Sample Alpine Linux image including Python and the Stackdriver Debugger agent.
# To build:
#   docker build . # Python 2.7
#   docker build --build-arg PYTHON_VERSION=3 . # Python 3.6
# The final image size should be around 50-60 MiB.

# Stage 1: Build the agent.
FROM alpine:latest

ARG PYTHON_VERSION=2
ENV PYTHON_VERSION=$PYTHON_VERSION
ENV PYTHON=python${PYTHON_VERSION}

RUN apk update
RUN apk add bash git curl gcc g++ make cmake ${PYTHON}-dev
RUN if [ $PYTHON_VERSION == "2" ]; then apk add py-setuptools; fi

RUN git clone https://github.com/GoogleCloudPlatform/cloud-debug-python
RUN PYTHON=$PYTHON bash cloud-debug-python/src/build.sh


# Stage 2: Create minimal image with just Python and the debugger.
FROM alpine:latest

ARG PYTHON_VERSION=2
ENV PYTHON_VERSION=$PYTHON_VERSION
ENV PYTHON=python${PYTHON_VERSION}

RUN apk --no-cache add $PYTHON libstdc++
RUN if [ $PYTHON_VERSION == "2" ]; then apk add --no-cache py-setuptools; fi

COPY --from=0 /cloud-debug-python/src/dist/*.egg .
RUN $PYTHON -m easy_install *.egg
RUN rm *.egg
