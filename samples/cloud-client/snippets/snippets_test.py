# Copyright 2022 Google Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import re

from _pytest.capture import CaptureFixture

import authenticate_explicit
import authenticate_implicit_with_adc
import idtoken_from_metadata_server
import idtoken_from_service_account
import verify_google_idtoken

import google
from google.oauth2 import service_account
import google.auth.transport.requests

CREDENTIALS, PROJECT = google.auth.default()


def test_authenticate_explicit(capsys: CaptureFixture):
    authenticate_explicit.authenticate_explicit(PROJECT)
    out, err = capsys.readouterr()
    assert re.search("Listed all storage buckets.", out)


def test_authenticate_implicit_with_adc(capsys: CaptureFixture):
    authenticate_implicit_with_adc.authenticate_implicit_with_adc(PROJECT)
    out, err = capsys.readouterr()
    assert re.search("Listing instances complete", out)


def test_idtoken_from_metadata_server(capsys: CaptureFixture):
    idtoken_from_metadata_server.idtoken_from_metadata_server("https://www.google.com")
    out, err = capsys.readouterr()
    assert re.search("Generated ID token.", out)


def test_idtoken_from_service_account(capsys: CaptureFixture):
    idtoken_from_service_account.get_idToken_from_serviceaccount(CREDENTIALS, "iap.googleapis.com")
    out, err = capsys.readouterr()
    assert re.search("Generated ID token.", out)


def test_verify_google_idtoken():
    idtoken = get_idtoken_from_service_account(CREDENTIALS, "iap.googleapis.com")

    verify_google_idtoken.verify_google_idtoken(
    idtoken,
    "iap.googleapis.com",
    "https://www.googleapis.com/oauth2/v3/certs"
    )


def get_idtoken_from_service_account(json_credential_path: str, target_audience: str):
    credentials = service_account.IDTokenCredentials.from_service_account_file(
        filename=json_credential_path,
        target_audience=target_audience)

    request = google.auth.transport.requests.Request()
    credentials.refresh(request)
    return credentials.token
