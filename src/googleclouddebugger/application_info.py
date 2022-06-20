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
"""Module to fetch information regarding the current application.

Some examples of the information the methods in this module fetch are platform
and region of the application.
"""

import enum
import os
import requests

# These environment variables will be set automatically by cloud functions
# depending on the runtime. If one of these values is set, we can infer that
# the current environment is GCF. Reference:
# https://cloud.google.com/functions/docs/env-var#runtime_environment_variables_set_automatically
_GCF_EXISTENCE_ENV_VARIABLES = ['FUNCTION_NAME', 'FUNCTION_TARGET']
_GCF_REGION_ENV_VARIABLE = 'FUNCTION_REGION'

_GCP_METADATA_REGION_URL = 'http://metadata/computeMetadata/v1/instance/region'
_GCP_METADATA_HEADER = {'Metadata-Flavor': 'Google'}


class PlatformType(enum.Enum):
  """The type of platform the application is running on.

  TODO: Define this enum in a common format for all agents to
  share. This enum needs to be maintained between the labels code generator
  and other agents, until there is a unified way to generate it.
  """
  CLOUD_FUNCTION = 'cloud_function'
  DEFAULT = 'default'


def GetPlatform():
  """Returns PlatformType for the current application."""

  # Check if it's a cloud function.
  for name in _GCF_EXISTENCE_ENV_VARIABLES:
    if name in os.environ:
      return PlatformType.CLOUD_FUNCTION

  # If we weren't able to identify the platform, fall back to default value.
  return PlatformType.DEFAULT


def GetRegion():
  """Returns region of the current application."""

  # If it's running cloud function with an old runtime.
  if _GCF_REGION_ENV_VARIABLE in os.environ:
    return os.environ.get(_GCF_REGION_ENV_VARIABLE)

  # Otherwise try fetching it from the metadata server.
  try:
    response = requests.get(
        _GCP_METADATA_REGION_URL, headers=_GCP_METADATA_HEADER)
    response.raise_for_status()
    # Example of response text: projects/id/regions/us-central1. So we strip
    # everything before the last /.
    region = response.text.split('/')[-1]
    if region == 'html>':
      # Sometimes we get an html response!
      return None

    return region
  except requests.exceptions.RequestException:
    return None
