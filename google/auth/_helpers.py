# Copyright 2015 Google Inc.
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

"""Helper functions for commonly used utilities."""


import calendar
import datetime

import six
from six.moves import urllib


def utcnow():
    """Returns the current UTC datetime.

    Returns:
        datetime: The current time in UTC.
    """
    return datetime.datetime.utcnow()


def datetime_to_secs(value):
    """Convert a datetime object to the number of seconds since the UNIX epoch.

    Args:
        value (datetime): The datetime to convert.

    Returns:
        int: The number of seconds since the UNIX epoch.
    """
    return calendar.timegm(value.utctimetuple())


def to_bytes(value, encoding='utf-8'):
    """Converts a string value to bytes, if necessary.

    Unfortunately, ``six.b`` is insufficient for this task since in
    Python 2 because it does not modify ``unicode`` objects.

    Args:
        value (Union[str, bytes]): The value to be converted.
        encoding (str): The encoding to use to convert unicode to bytes.
            Defaults to "utf-8".

    Returns:
        bytes: The original value converted to bytes (if unicode) or as
            passed in if it started out as bytes.

    Raises:
        ValueError: If the value could not be converted to bytes.
    """
    result = (value.encode(encoding)
              if isinstance(value, six.text_type) else value)
    if isinstance(result, six.binary_type):
        return result
    else:
        raise ValueError('{0!r} could not be converted to bytes'.format(value))


def from_bytes(value):
    """Converts bytes to a string value, if necessary.

    Args:
        value (Union[str, bytes]): The value to be converted.

    Returns:
        str: The original value converted to unicode (if bytes) or as passed in
            if it started out as unicode.

    Raises:
        ValueError: If the value could not be converted to unicode.
    """
    result = (value.decode('utf-8')
              if isinstance(value, six.binary_type) else value)
    if isinstance(result, six.text_type):
        return result
    else:
        raise ValueError(
            '{0!r} could not be converted to unicode'.format(value))


def update_query(url, params):
    """Updates a URL's query parameters
    Replaces any current values if they are already present in the URL.
    Args:
        url (str): The URL to update.
        params (Mapping): A mapping of query parameter keys to values.
    Returns:
        str: The URL with updated query parameters.
    """
    # Split the URL into parts.
    parts = urllib.parse.urlparse(url)
    # Parse the query string.
    query_params = urllib.parse.parse_qs(parts.query)
    # Update the query parameters with the new parameters.
    query_params.update(params)
    # Remove any None values.
    query_params = {
        key: value for key, value
        in six.iteritems(query_params)
        if value is not None}
    # Re-encoded the query string.
    new_query = urllib.parse.urlencode(query_params, doseq=True)
    # Unsplit the url.
    new_parts = parts[:4] + (new_query,) + parts[5:]
    return urllib.parse.urlunparse(new_parts)
