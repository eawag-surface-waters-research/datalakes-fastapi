from datetime import UTC, datetime

import pytest

from app.backends import RcloneRemote
from app.rclone import SourceListing, connection_string, parse_item, quote, rclone_env


def test_connection_string():
  remote = RcloneRemote(type="s3", params={"provider": "AWS", "env_auth": "false"}, root="bucket/prefix")
  assert connection_string(remote) == ":s3,provider='AWS',env_auth='false':bucket/prefix"


def test_quote_doubles_quotes_so_a_value_cant_add_options():
  assert quote("a',access_key_id='x") == "'a'',access_key_id=''x'"
  assert quote("a,b:c") == "'a,b:c'"


def test_connection_string_escapes_values():
  remote = RcloneRemote(type="s3", params={"endpoint": "https://s3.example.org/a',access_key_id='x"}, root="bucket")
  assert connection_string(remote) == ":s3,endpoint='https://s3.example.org/a'',access_key_id=''x':bucket"


@pytest.mark.parametrize(
  "remote",
  [
    RcloneRemote(type="s3,access_key_id=x", params={}, root=""),
    RcloneRemote(type="s3", params={"provider=AWS,access_key_id": "x"}, root=""),
  ],
)
def test_connection_string_rejects_invalid_names(remote):
  """Backend and parameter names come from the backend's code, not from users, but they can't be quoted, so they're checked too."""
  with pytest.raises(ValueError):
    connection_string(remote)


def test_rclone_env_leaves_out_credentials(monkeypatch):
  monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAIOSFODNN7EXAMPLE")
  monkeypatch.setenv("RCLONE_S3_ACCESS_KEY_ID", "AKIAIOSFODNN7EXAMPLE")
  assert set(rclone_env()) == {"PATH", "HOME"}


def listing_of(*lines: str) -> SourceListing:
  listing = SourceListing()
  for line in lines:
    item = parse_item(line)
    assert item is not None
    listing.add(item)
  return listing


def test_parse_a_file():
  line = (
    '{"Path":"a.nc","Name":"a.nc","Size":18342011,"MimeType":"application/x-netcdf",'
    '"ModTime":"2024-06-01T02:12:40.5+02:00","IsDir":false,"Tier":"STANDARD"},'
  )
  listing = listing_of(line)
  assert listing.folder_count == 0
  [entry] = listing.files
  assert entry.path == "a.nc"
  assert entry.size == 18342011
  assert entry.modified_at == datetime(2024, 6, 1, 0, 12, 40, 500000, tzinfo=UTC)


def test_parse_a_file_without_modification_time():
  [entry] = listing_of('{"Path":"a.nc","Name":"a.nc","Size":0,"MimeType":"application/x-netcdf","IsDir":false}').files
  assert entry.size == 0
  assert entry.modified_at is None


def test_parse_a_folder():
  listing = listing_of('{"Path":"2024","Name":"2024","Size":-1,"MimeType":"inode/directory","ModTime":"2000-01-01T00:00:00Z","IsDir":true},')
  assert listing.files == []
  assert listing.folders == ["2024"]
  assert listing.folder_count == 1


def test_lines_that_are_not_items_are_skipped():
  assert parse_item("[") is None
  assert parse_item("]") is None
