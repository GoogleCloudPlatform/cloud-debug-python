#
# Base image
#
FROM google/debian:wheezy

#
# Dependencies
#

# Everything we can get through apt-get
RUN apt-get update
RUN apt-get -y -q --no-install-recommends install apt-utils
RUN apt-get -y -q --no-install-recommends install ca-certificates curl gcc build-essential unzip cmake python python-dev python-setuptools

# gflags (https://code.google.com/p/gflags/)
RUN mkdir /tmp/gflags && cd /tmp/gflags && curl -Lk https://github.com/gflags/gflags/archive/v2.1.2.tar.gz -o gflags.tar.gz && tar xzvf gflags.tar.gz && cd gflags-2.1.2 && mkdir build && cd build && cmake -DCMAKE_CXX_FLAGS=-fpic -DGFLAGS_NAMESPACE=google .. && make && make install && cd ~ && rm -rf /tmp/gflags

# google-glog (https://code.google.com/p/google-glog/)
RUN mkdir /tmp/glog && cd /tmp/glog && curl -L https://github.com/google/glog/archive/v0.3.4.tar.gz -o glog.tar.gz && tar xzvf glog.tar.gz && cd glog-0.3.4 && ./configure --with-pic && make && make install && cd ~ && rm -rf /tmp/glog

