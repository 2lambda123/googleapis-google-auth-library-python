# Copyright 2022 Google LLC
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

import mock
import pytest  # type: ignore
from six.moves import http_client

from google.auth import exceptions
from google.auth import external_account_authorized_user
from google.auth import transport


class TestCredentials(object):
    TOKEN_URL = "https://sts.googleapis.com/v1/token"
    TOKEN_INFO_URL = "https://sts.googleapis.com/v1/introspect"
    REVOKE_URL = "https://sts.googleapis.com/v1/revoke"
    PROJECT_NUMBER = "123456"
    QUOTA_PROJECT_ID = "654321"
    POOL_ID = "POOL_ID"
    PROVIDER_ID = "PROVIDER_ID"
    AUDIENCE = (
        "//iam.googleapis.com/projects/{}"
        "/locations/global/workloadIdentityPools/{}"
        "/providers/{}"
    ).format(PROJECT_NUMBER, POOL_ID, PROVIDER_ID)
    REFRESH_TOKEN = "REFRESH_TOKEN"
    NEW_REFRESH_TOKEN = "NEW_REFRESH_TOKEN"
    ACCESS_TOKEN = "ACCESS_TOKEN"
    CLIENT_ID = "username"
    CLIENT_SECRET = "password"
    # Base64 encoding of "username:password".
    BASIC_AUTH_ENCODING = "dXNlcm5hbWU6cGFzc3dvcmQ="

    @classmethod
    def make_credentials(
        cls,
        audience=None,
        refresh_token=None,
        token_url=None,
        token_info_url=None,
        client_id=None,
        client_secret=None,
        token=None,
        expiry=None,
        revoke_url=None,
        quota_project_id=None,
    ):
        return external_account_authorized_user.Credentials(
            audience=(audience or cls.AUDIENCE),
            refresh_token=(refresh_token or cls.REFRESH_TOKEN),
            token_url=(token_url or cls.TOKEN_URL),
            token_info_url=(token_info_url or cls.TOKEN_INFO_URL),
            client_id=(client_id or cls.CLIENT_ID),
            client_secret=(client_secret or cls.CLIENT_SECRET),
            token=token,
            expiry=expiry,
            revoke_url=revoke_url,
            quota_project_id=quota_project_id,
        )

    @classmethod
    def make_mock_request(cls, status=http_client.OK, data=None):
        # STS token exchange request.
        token_response = mock.create_autospec(transport.Response, instance=True)
        token_response.status = status
        token_response.data = json.dumps(data).encode("utf-8")
        responses = [token_response]

        request = mock.create_autospec(transport.Request)
        request.side_effect = responses

        return request

    def test_default_state(self):
        creds = self.make_credentials()

        assert not creds.expiry
        assert not creds.expired
        assert not creds.token
        assert not creds.valid
        assert not creds.requires_scopes
        assert creds.is_user

    @mock.patch("google.auth._helpers.utcnow", return_value=datetime.datetime.min)
    def test_refresh_auth_success(self, utcnow):
        request = self.make_mock_request(
            status=http_client.OK,
            data={"access_token": self.ACCESS_TOKEN, "expires_in": 3600},
        )
        creds = self.make_credentials()

        creds.refresh(request)

        assert creds.expiry == utcnow() + datetime.timedelta(seconds=3600)
        assert not creds.expired
        assert creds.token == self.ACCESS_TOKEN
        assert creds.valid
        assert not creds.requires_scopes
        assert creds.is_user
        assert creds._refresh_token == self.REFRESH_TOKEN

        request.assert_called_once_with(
            url=self.TOKEN_URL,
            method="POST",
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Authorization": "Basic " + self.BASIC_AUTH_ENCODING,
            },
            body=bytes(
                "grant_type=refresh_token&refresh_token=" + self.REFRESH_TOKEN, "UTF-8"
            ),
        )

    @mock.patch("google.auth._helpers.utcnow", return_value=datetime.datetime.min)
    def test_refresh_auth_success_new_refresh_token(self, utcnow):
        request = self.make_mock_request(
            status=http_client.OK,
            data={
                "access_token": self.ACCESS_TOKEN,
                "expires_in": 3600,
                "refresh_token": self.NEW_REFRESH_TOKEN,
            },
        )
        creds = self.make_credentials()

        creds.refresh(request)

        assert creds.expiry == utcnow() + datetime.timedelta(seconds=3600)
        assert not creds.expired
        assert creds.token == self.ACCESS_TOKEN
        assert creds.valid
        assert not creds.requires_scopes
        assert creds.is_user
        assert creds._refresh_token == self.NEW_REFRESH_TOKEN

        request.assert_called_once_with(
            url=self.TOKEN_URL,
            method="POST",
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Authorization": "Basic " + self.BASIC_AUTH_ENCODING,
            },
            body=bytes(
                "grant_type=refresh_token&refresh_token=" + self.REFRESH_TOKEN, "UTF-8"
            ),
        )

    def test_refresh_auth_failure(self):
        request = self.make_mock_request(
            status=http_client.BAD_REQUEST,
            data={
                "error": "invalid_request",
                "error_description": "Invalid subject token",
                "error_uri": "https://tools.ietf.org/html/rfc6749",
            },
        )
        creds = self.make_credentials()

        with pytest.raises(exceptions.OAuthError) as excinfo:
            creds.refresh(request)

        assert excinfo.match(
            r"Error code invalid_request: Invalid subject token - https://tools.ietf.org/html/rfc6749"
        )

        assert not creds.expiry
        assert not creds.expired
        assert not creds.token
        assert not creds.valid
        assert not creds.requires_scopes
        assert creds.is_user

        request.assert_called_once_with(
            url=self.TOKEN_URL,
            method="POST",
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Authorization": "Basic " + self.BASIC_AUTH_ENCODING,
            },
            body=bytes(
                "grant_type=refresh_token&refresh_token=" + self.REFRESH_TOKEN, "UTF-8"
            ),
        )

    def test_info(self):
        creds = self.make_credentials()
        info = creds.info

        assert info["audience"] == self.AUDIENCE
        assert info["refresh_token"] == self.REFRESH_TOKEN
        assert info["token_url"] == self.TOKEN_URL
        assert info["token_info_url"] == self.TOKEN_INFO_URL
        assert info["client_id"] == self.CLIENT_ID
        assert info["client_secret"] == self.CLIENT_SECRET
        assert "token" not in info
        assert "expiry" not in info
        assert "revoke_url" not in info
        assert "quota_project_id" not in info

    def test_info_full(self):
        creds = self.make_credentials(
            token=self.ACCESS_TOKEN,
            expiry=datetime.datetime.min,
            revoke_url=self.REVOKE_URL,
            quota_project_id=self.QUOTA_PROJECT_ID,
        )
        info = creds.info

        assert info["audience"] == self.AUDIENCE
        assert info["refresh_token"] == self.REFRESH_TOKEN
        assert info["token_url"] == self.TOKEN_URL
        assert info["token_info_url"] == self.TOKEN_INFO_URL
        assert info["client_id"] == self.CLIENT_ID
        assert info["client_secret"] == self.CLIENT_SECRET
        assert info["token"] == self.ACCESS_TOKEN
        assert info["expiry"] == datetime.datetime.min.isoformat() + "Z"
        assert info["revoke_url"] == self.REVOKE_URL
        assert info["quota_project_id"] == self.QUOTA_PROJECT_ID

    def test_get_project_id(self):
        creds = self.make_credentials()
        assert creds.get_project_id() is None

    def test_with_quota_project(self):
        creds = self.make_credentials(
            token=self.ACCESS_TOKEN,
            expiry=datetime.datetime.min,
            revoke_url=self.REVOKE_URL,
            quota_project_id=self.QUOTA_PROJECT_ID,
        )
        new_creds = creds.with_quota_project(self.QUOTA_PROJECT_ID)
        assert new_creds._audience == creds._audience
        assert new_creds._refresh_token == creds._refresh_token
        assert new_creds._token_url == creds._token_url
        assert new_creds._token_info_url == creds._token_info_url
        assert new_creds._client_id == creds._client_id
        assert new_creds._client_secret == creds._client_secret
        assert new_creds.token == creds.token
        assert new_creds.expiry == creds.expiry
        assert new_creds._revoke_url == creds._revoke_url
        assert new_creds._quota_project_id == self.QUOTA_PROJECT_ID

    def test_with_token_uri(self):
        creds = self.make_credentials(
            token=self.ACCESS_TOKEN,
            expiry=datetime.datetime.min,
            revoke_url=self.REVOKE_URL,
            quota_project_id=self.QUOTA_PROJECT_ID,
        )
        new_creds = creds.with_token_uri("https://google.com")
        assert new_creds._audience == creds._audience
        assert new_creds._refresh_token == creds._refresh_token
        assert new_creds._token_url == "https://google.com"
        assert new_creds._token_info_url == creds._token_info_url
        assert new_creds._client_id == creds._client_id
        assert new_creds._client_secret == creds._client_secret
        assert new_creds.token == creds.token
        assert new_creds.expiry == creds.expiry
        assert new_creds._revoke_url == creds._revoke_url
        assert new_creds._quota_project_id == creds._quota_project_id

    def test_from_file_required_options_only(self, tmpdir):
        from_creds = self.make_credentials()
        config_file = tmpdir.join("config.json")
        config_file.write(json.dumps(from_creds.info))
        creds = external_account_authorized_user.Credentials.from_file(str(config_file))

        assert isinstance(creds, external_account_authorized_user.Credentials)
        assert creds._audience == self.AUDIENCE
        assert creds._refresh_token == self.REFRESH_TOKEN
        assert creds._token_url == self.TOKEN_URL
        assert creds._token_info_url == self.TOKEN_INFO_URL
        assert creds._client_id == self.CLIENT_ID
        assert creds._client_secret == self.CLIENT_SECRET
        assert creds.token is None
        assert creds.expiry is None
        assert creds._revoke_url is None
        assert creds._quota_project_id is None

    def test_from_file_full_options(self, tmpdir):
        from_creds = self.make_credentials(
            token=self.ACCESS_TOKEN,
            expiry=datetime.datetime.min,
            revoke_url=self.REVOKE_URL,
            quota_project_id=self.QUOTA_PROJECT_ID,
        )
        config_file = tmpdir.join("config.json")
        config_file.write(json.dumps(from_creds.info))
        creds = external_account_authorized_user.Credentials.from_file(str(config_file))

        assert isinstance(creds, external_account_authorized_user.Credentials)
        assert creds._audience == self.AUDIENCE
        assert creds._refresh_token == self.REFRESH_TOKEN
        assert creds._token_url == self.TOKEN_URL
        assert creds._token_info_url == self.TOKEN_INFO_URL
        assert creds._client_id == self.CLIENT_ID
        assert creds._client_secret == self.CLIENT_SECRET
        assert creds.token == self.ACCESS_TOKEN
        assert creds.expiry == datetime.datetime.min
        assert creds._revoke_url == self.REVOKE_URL
        assert creds._quota_project_id == self.QUOTA_PROJECT_ID
