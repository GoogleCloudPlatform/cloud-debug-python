#!/bin/bash -e

# Build and install gflags.
mkdir /tmp/gflags
cd /tmp/gflags
curl -Lk https://github.com/gflags/gflags/archive/v2.1.2.tar.gz -o gflags.tar.gz
tar xzvf gflags.tar.gz
cd gflags-2.1.2
mkdir build
cd build
cmake -DCMAKE_CXX_FLAGS=-fpic -DGFLAGS_NAMESPACE=google ..
make
make install
cd ~
rm -rf /tmp/gflags

# Build and install glog.
mkdir /tmp/glog
cd /tmp/glog
curl -L https://github.com/google/glog/archive/v0.3.4.tar.gz -o glog.tar.gz
tar xzvf glog.tar.gz
cd glog-0.3.4
./configure --with-pic
make
make install
cd ~
rm -rf /tmp/glog
