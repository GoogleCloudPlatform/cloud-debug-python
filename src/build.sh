#!/bin/bash -e
#
# Copyright 2015 Google Inc. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

#
# This script builds the Python Cloud Debugger agent from source code. The
# debugger is currently only supported on Linux.
#
# The build script assumes Python, cmake, curl and gcc are installed.
# To install those on Debian, run this commandd:
# sudo apt-get install curl ca-certificates gcc build-essential cmake \
#                      python python-dev libpython2.7 python-setuptools
#
# The Python Cloud Debugger agent uses glog and gflags libraries. We build them
# first. Then we use setuptools to build the debugger agent. The entire
# build process is local and does not change any system directories.
#
# Home page of gflags: https://github.com/gflags/gflags
# Home page of glog: https://github.com/google/glog
#

GFLAGS_URL=https://github.com/gflags/gflags/archive/v2.1.2.tar.gz
GLOG_URL=https://github.com/google/glog/archive/v0.3.4.tar.gz

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

# Create setup.cfg file and point to the third_party libraries we just built.
echo "[global]
verbose=1

[build_ext]
include_dirs=${ROOT}/build/third_party/include
library_dirs=${ROOT}/build/third_party/lib" > ${ROOT}/setup.cfg

# Build the Python Cloud Debugger agent.
pushd ${ROOT}
python setup.py bdist_egg
popd

