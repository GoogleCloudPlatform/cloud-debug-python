GFLAGS_URL=https://github.com/gflags/gflags/archive/v2.1.2.tar.gz
GLOG_URL=https://github.com/google/glog/archive/v0.3.4.tar.gz

SUPPORTED_VERSIONS=(cp36-cp36m cp37-cp37m cp38-cp38 cp39-cp39)

ROOT=$(cd $(dirname "${BASH_SOURCE[0]}") >/dev/null; /bin/pwd -P)

# Parallelize the build over N threads where N is the number of cores * 1.5.
PARALLEL_BUILD_OPTION="-j $(($(nproc 2> /dev/null || echo 4)*3/2))"

# Clean up any previous build files.
rm -rf ${ROOT}/build ${ROOT}/dist ${ROOT}/setup.cfg

# Create directory for third-party libraries.
mkdir -p ${ROOT}/build/third_party

# Build and install gflags to build/third_party.
pushd ${ROOT}/build/third_party
curl -Lk ${GFLAGS_URL} -o gflags.tar.gz
tar xzvf gflags.tar.gz
cd gflags-*
mkdir build
cd build
cmake -DCMAKE_CXX_FLAGS=-fpic \
      -DGFLAGS_NAMESPACE=google \
      -DCMAKE_INSTALL_PREFIX:PATH=${ROOT}/build/third_party \
      ..
make ${PARALLEL_BUILD_OPTION}
make install
popd

# Build and install glog to build/third_party.
pushd ${ROOT}/build/third_party
curl -L ${GLOG_URL} -o glog.tar.gz
tar xzvf glog.tar.gz
cd glog-*
./configure --with-pic \
            --prefix=${ROOT}/build/third_party \
            --with-gflags=${ROOT}/build/third_party
make ${PARALLEL_BUILD_OPTION}
make install
popd

# Extract build version from version.py
grep "^ *__version__ *=" "/io/src/googleclouddebugger/version.py" | grep -Eo "[0-9.]+" > "version.txt"
AGENT_VERSION=$(cat "version.txt")
echo "Building distribution packages for python agent version ${AGENT_VERSION}"

# Create setup.cfg file and point to the third_party libraries we just build.
echo "[global]
verbose=1

[build_ext]
include_dirs=${ROOT}/build/third_party/include
library_dirs=${ROOT}/build/third_party/lib" > ${ROOT}/setup.cfg

# Build the Python Cloud Debugger agent.
pushd ${ROOT}

for PY_VERSION in ${SUPPORTED_VERSIONS[@]}; do
    echo "Building the ${PY_VERSION} agent"
    "/opt/python/${PY_VERSION}/bin/pip" install -r /io/requirements_dev.txt
    "/opt/python/${PY_VERSION}/bin/pip" wheel /io/src --no-deps -w /tmp/dist/
    PACKAGE_NAME="google_python_cloud_debugger-${AGENT_VERSION}"
    WHL_FILENAME="${PACKAGE_NAME}-${PY_VERSION}-linux_x86_64.whl"
    auditwheel repair "/tmp/dist/${WHL_FILENAME}" -w /io/dist/

    echo "Running tests"
    "/opt/python/${PY_VERSION}/bin/pip" install google-python-cloud-debugger --no-index -f /io/dist
    (cd "$HOME"; "/opt/python/${PY_VERSION}/bin/pytest" /io/tests)
done

popd

# Clean up temporary directories.
rm -rf ${ROOT}/build ${ROOT}/setup.cfg
echo "Build artifacts are in the dist directory"

