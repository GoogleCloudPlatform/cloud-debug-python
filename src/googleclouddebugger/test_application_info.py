"""Tests for application_info."""

import os
from unittest import mock

import requests

from googleclouddebugger import application_info
from absl.testing import absltest


class ApplicationInfoTest(absltest.TestCase):

  def test_get_platform_default(self):
    """Returns default platform when no platform is detected."""
    self.assertEqual(application_info.PlatformType.DEFAULT,
                     application_info.GetPlatform())

  def test_get_platform_gcf_name(self):
    """Returns cloud_function when the FUNCTION_NAME env variable is set."""
    try:
      os.environ['FUNCTION_NAME'] = 'function-name'
      self.assertEqual(application_info.PlatformType.CLOUD_FUNCTION,
                       application_info.GetPlatform())
    finally:
      del os.environ['FUNCTION_NAME']

  def test_get_platform_gcf_target(self):
    """Returns cloud_function when the FUNCTION_TARGET env variable is set."""
    try:
      os.environ['FUNCTION_TARGET'] = 'function-target'
      self.assertEqual(application_info.PlatformType.CLOUD_FUNCTION,
                       application_info.GetPlatform())
    finally:
      del os.environ['FUNCTION_TARGET']

  def test_get_region_none(self):
    """Returns None when no region is detected."""
    self.assertIsNone(application_info.GetRegion())

  def test_get_region_gcf(self):
    """Returns correct region when the FUNCTION_REGION env variable is set."""
    try:
      os.environ['FUNCTION_REGION'] = 'function-region'
      self.assertEqual('function-region',
                       application_info.GetRegion())
    finally:
      del os.environ['FUNCTION_REGION']

  @mock.patch('requests.get')
  def test_get_region_metadata_server(self, mock_requests_get):
    """Returns correct region if found in metadata server."""
    success_response = mock.Mock(requests.Response)
    success_response.status_code = 200
    success_response.text = 'a/b/function-region'
    mock_requests_get.return_value = success_response

    self.assertEqual('function-region', application_info.GetRegion())

  @mock.patch('requests.get')
  def test_get_region_metadata_server_fail(self, mock_requests_get):
    """Returns None if region not found in metadata server."""
    exception = requests.exceptions.HTTPError()
    failed_response = mock.Mock(requests.Response)
    failed_response.status_code = 400
    failed_response.raise_for_status.side_effect = exception
    mock_requests_get.return_value = failed_response

    self.assertIsNone(application_info.GetRegion())

if __name__ == '__main__':
  absltest.main()
