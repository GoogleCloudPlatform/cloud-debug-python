# Copyright 2015 Google Inc. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS-IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Python Cloud Debugger build and packaging script."""

from glob import glob
import os
import ConfigParser

from setuptools import setup, Extension
from distutils import sysconfig
import googleclouddebugger


def remove_prefixes(optlist, bad_prefixes):
  for bad_prefix in bad_prefixes:
    for i, flag in enumerate(optlist):
      if flag.startswith(bad_prefix):
        optlist.pop(i)
        break
  return optlist


readme = os.path.join(os.path.dirname(__file__), '../README.md')
LONG_DESCRIPTION = open(readme).read()

try:
  config = ConfigParser.ConfigParser()
  config.read('setup.cfg')
  lib_dirs = config.get('build_ext', 'library_dirs').split(':')
except:  # pylint: disable=bare-except
  lib_dirs = [sysconfig.get_config_var('LIBDIR')]

static_libs = []
deps = ['libgflags.a', 'libglog.a']
for dep in deps:
  for lib_dir in lib_dirs:
    path = os.path.join(lib_dir, dep)
    if os.path.isfile(path):
      static_libs.append(path)
assert len(static_libs) == len(deps), (static_libs, deps, lib_dirs)

cvars = sysconfig.get_config_vars()
cvars['OPT'] = str.join(' ', remove_prefixes(
    cvars.get('OPT').split(),
    ['-g', '-O', '-Wstrict-prototypes']))

cdbg_native_module = Extension(
    'googleclouddebugger.cdbg_native',
    sources=glob('googleclouddebugger/*.cc'),
    extra_compile_args=[
        '-std=c++11',
        '-Werror',
        '-g0',
        '-O3',
    ],
    extra_link_args=static_libs,
    libraries=['rt'])

setup(
    name='google-python-cloud-debugger',
    description='Python Cloud Debugger',
    long_description=LONG_DESCRIPTION,
    url='https://github.com/GoogleCloudPlatform/cloud-debug-python',
    author='Google Inc.',
    version=googleclouddebugger.__version__,
    install_requires=['google-api-python-client'],
    packages=['googleclouddebugger'],
    ext_modules=[cdbg_native_module],
    license="Apache License, Version 2.0",
    keywords='google cloud debugger',
    classifiers=[
        'Programming Language :: Python :: 2.7',
        'Development Status :: 3 - Alpha',
        'Intended Audience :: Developers'
    ])
