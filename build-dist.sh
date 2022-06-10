DOCKER_IMAGE='quay.io/pypa/manylinux2010_x86_64'

docker pull "$DOCKER_IMAGE"
docker container run -t --rm -v "$(pwd)":/io "$DOCKER_IMAGE" /io/src/build-wheels.sh
