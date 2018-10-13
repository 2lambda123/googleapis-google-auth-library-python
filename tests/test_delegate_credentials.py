# Copyright 2018 Google Inc.
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

import datetime
import json
import os

import mock
import pytest
from six.moves import http_client

from google.auth import _helpers
from google.auth import crypt
from google.auth import exceptions
from google.auth import transport
from google.auth.delegate_credentials import DelegateCredentials
from google.oauth2 import service_account

DATA_DIR = os.path.join(os.path.dirname(__file__), '', 'data')

with open(os.path.join(DATA_DIR, 'privatekey.pem'), 'rb') as fh:
    PRIVATE_KEY_BYTES = fh.read()

SERVICE_ACCOUNT_JSON_FILE = os.path.join(DATA_DIR, 'service_account.json')

with open(SERVICE_ACCOUNT_JSON_FILE, 'r') as fh:
    SERVICE_ACCOUNT_INFO = json.load(fh)

SIGNER = crypt.RSASigner.from_string(PRIVATE_KEY_BYTES, '1')


class TestDelegateCredentials(object):

    SERVICE_ACCOUNT_EMAIL = 'service-account@example.com'
    IMPERSONATED_ACCOUNT = 'impersonated@project.iam.gserviceaccount.com'
    NEW_SCOPES = ['https://www.googleapis.com/auth/devstorage.read_only']
    DELEGATES = []
    NEW_SCOPES = 3600

    TOKEN_URI = 'https://example.com/oauth2/token'

    @classmethod
    def make_credentials(cls):
        root_credentials = service_account.Credentials(
            SIGNER, cls.SERVICE_ACCOUNT_EMAIL, cls.TOKEN_URI)
        return DelegateCredentials(
              root_credentials=root_credentials,
              principal=cls.IMPERSONATED_ACCOUNT,
              new_scopes=cls.NEW_SCOPES,
              delegates=cls.DELEGATES,
              lifetime=cls.NEW_SCOPES)

    def test_default_state(self):
        credentials = self.make_credentials()
        assert not credentials.valid
        assert credentials.expired

    def make_request(self, data, status=http_client.OK,
                     headers=None, side_effect=None):
        response = mock.create_autospec(transport.Response, instance=False)
        response.status = status
        response.data = _helpers.to_bytes(data)
        response.headers = headers or {}

        request = mock.create_autospec(transport.Request, instance=False)
        request.side_effect = side_effect
        request.return_value = response

        return request

    @mock.patch('google.oauth2._client.jwt_grant', autospec=True)
    def test_refresh_success(self, jwt_grant):
        credentials = self.make_credentials()
        token = 'token'

        jwt_grant.return_value = (
            token,
            _helpers.utcnow() + datetime.timedelta(seconds=500),
            {})

        response_body = {
            "accessToken": token,
            "expireTime": (_helpers.utcnow().replace(microsecond=0) +
                           datetime.timedelta(seconds=credentials._lifetime)
                           ).isoformat('T') + 'Z'
        }

        request = self.make_request(data=json.dumps(response_body),
                                    status=http_client.OK)
        credentials.refresh(request)

        assert credentials.valid
        assert not credentials.expired

    @mock.patch('google.oauth2._client.jwt_grant', autospec=True)
    def test_refresh_failure_unauthorzed(self, jwt_grant):
        credentials = self.make_credentials()
        token = 'token'

        jwt_grant.return_value = (
            token,
            _helpers.utcnow() + datetime.timedelta(seconds=500),
            {})

        response_body = {
            "error": {
              "code": 403,
              "message": "The caller does not have permission",
              "status": "PERMISSION_DENIED"
            }
        }

        request = self.make_request(data=json.dumps(response_body),
                                    status=http_client.UNAUTHORIZED)

        with pytest.raises(exceptions.DefaultCredentialsError) as excinfo:
            credentials.refresh(request)

        assert excinfo.match(r'Unable to acquire delegated credentials')

        assert not credentials.valid
        assert credentials.expired

    @mock.patch('google.oauth2._client.jwt_grant', autospec=True)
    def test_refresh_failure_http_error(self, jwt_grant):
        credentials = self.make_credentials()
        token = 'token'

        jwt_grant.return_value = (
            token,
            _helpers.utcnow() + datetime.timedelta(seconds=500),
            {})

        response_body = {}

        request = self.make_request(data=json.dumps(response_body),
                                    status=http_client.HTTPException,
                                    side_effect=exceptions.TransportError)

        with pytest.raises(exceptions.DefaultCredentialsError) as excinfo:
            credentials.refresh(request)

        assert excinfo.match(r'Unable to acquire delegated credentials')

        assert not credentials.valid
        assert credentials.expired

    def test_expired(self):
        credentials = self.make_credentials()
        assert credentials.expired
