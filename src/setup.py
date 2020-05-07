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

# pylint: disable=g-statement-before-imports,g-import-not-at-top
try:
  from ConfigParser import ConfigParser  # Python 2
except ImportError:
  from configparser import ConfigParser  # Python 3
# pylint: enable=g-statement-before-imports,g-import-not-at-top
from glob import glob
import os
import re
import sys
from distutils import sysconfig
from setuptools import Extension
from setuptools import setup


def RemovePrefixes(optlist, bad_prefixes):
  for bad_prefix in bad_prefixes:
    for i, flag in enumerate(optlist):
      if flag.startswith(bad_prefix):
        optlist.pop(i)
        break
  return optlist


def ReadConfig(section, value, default):
  try:
    config = ConfigParser()
    config.read('setup.cfg')
    return config.get(section, value)
  except:  # pylint: disable=bare-except
    return default


LONG_DESCRIPTION = (
    'The Cloud Debugger lets you inspect the state of an application at any\n'
    'code location without stopping or slowing it down. The debugger makes it\n'
    'easier to view the application state without adding logging statements.\n'
    '\n'
    'For more details please see '
    'https://github.com/GoogleCloudPlatform/cloud-debug-python\n')

lib_dirs = ReadConfig('build_ext',
                      'library_dirs',
                      sysconfig.get_config_var('LIBDIR')).split(':')
extra_compile_args = ReadConfig('cc_options', 'extra_compile_args', '').split()
extra_link_args = ReadConfig('cc_options', 'extra_link_args', '').split()

static_libs = []
deps = ['libgflags.a', 'libglog.a']
for dep in deps:
  for lib_dir in lib_dirs:
    path = os.path.join(lib_dir, dep)
    if os.path.isfile(path):
      static_libs.append(path)
assert len(static_libs) == len(deps), (static_libs, deps, lib_dirs)

cvars = sysconfig.get_config_vars()
cvars['OPT'] = str.join(' ', RemovePrefixes(
    cvars.get('OPT').split(),
    ['-g', '-O', '-Wstrict-prototypes']))

# Determine the current version of the package. The easiest way would be to
# import "googleclouddebugger" and read its __version__ attribute.
# Unfortunately we can't do that because "googleclouddebugger" depends on
# "cdbg_native" that hasn't been built yet.
version = None
with open('googleclouddebugger/version.py', 'r') as version_file:
  version_pattern = re.compile(r"^\s*__version__\s*=\s*'([0-9.]*)'")
  for line in version_file:
    match = version_pattern.match(line)
    if match:
      version = match.groups()[0]
assert version

cdbg_native_module = Extension(
    'googleclouddebugger.cdbg_native',
    sources=glob('googleclouddebugger/*.cc'),
    extra_compile_args=[
        '-std=c++0x',
        '-Werror',
        '-g0',
        '-O3',
    ] + extra_compile_args,
    extra_link_args=static_libs + extra_link_args,
    libraries=['rt'])

setup(
    name='google-python-cloud-debugger',
    description='Python Cloud Debugger',
    long_description=LONG_DESCRIPTION,
    url='https://github.com/GoogleCloudPlatform/cloud-debug-python',
    author='Google Inc.',
    version=version,
    install_requires=[
        'google-api-python-client',
        'google-auth==1.8.2'
        if sys.version_info.major < 3 else 'google-auth>=1.0.0',
        'google-auth-httplib2',
        'pyyaml',
        'six>=1.10.0',
    ],
    packages=['googleclouddebugger'],
    ext_modules=[cdbg_native_module],
    license='Apache License, Version 2.0',
    keywords='google cloud debugger',
    classifiers=[
        'Programming Language :: Python :: 2.7',
        'Programming Language :: Python :: 3.6',
        'Programming Language :: Python :: 3.7',
        'Programming Language :: Python :: 3.8',
        'Development Status :: 3 - Alpha',
        'Intended Audience :: Developers',
    ])
