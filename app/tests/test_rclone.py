from datetime import UTC, datetime

from app.rclone import build_remote, parse_entry, quote, rclone_env
from app.schemas import S3SourceInput, SwitchdriveSourceInput

# sample rclone error outputs
NOT_FOUND = "2026/11/09 11:08:03 Failed to lsjson: error in ListJSON: directory not found\n"
ACCESS_DENIED = "2026/11/09 11:08:03 Failed to lsjson: error in ListJSON: AccessDenied: Access Denied\n\tstatus code: 403, request id: DPXP403VQ909KYS7, host id: x\n"
UNREACHABLE = (
  "2026/11/09 11:08:16 ERROR : : error listing: RequestError: send request failed\n"
  'caused by: Get "https://nonexistent.invalid/x": dial tcp: lookup nonexistent.invalid on 1.1.1.1:53: no such host\n'
)
UNAUTHORIZED = (
  "2026/11/09 11:09:36 Failed to lsjson with 2 errors: last error was: error in ListJSON: couldn't list files: Username or password was incorrect: "
  "Sabre\\DAV\\Exception\\NotAuthenticated: 401 Unauthorized\n"
)


def s3(**options) -> S3SourceInput:
  path = options.pop("path", "")
  return S3SourceInput.model_validate({"backend": "s3", "options": {"bucket": "eawag-lexplore", **options}, "path": path})


def switchdrive(path: str = "") -> SwitchdriveSourceInput:
  return SwitchdriveSourceInput.model_validate(
    {"backend": "switchdrive", "options": {"share_url": "https://drive.switch.ch/index.php/s/aB3dE5fG7hJ9kL1"}, "path": path}
  )


def test_build_remote_for_amazon_s3():
  remote = build_remote(s3(region="eu-central-1", path="thermistorchain/Level2/"))
  assert remote == ":s3,provider='AWS',env_auth='false',region='eu-central-1':eawag-lexplore/thermistorchain/Level2"


def test_build_remote_for_the_whole_bucket():
  assert build_remote(s3()) == ":s3,provider='AWS',env_auth='false':eawag-lexplore"


def test_build_remote_for_an_s3_compatible_store():
  remote = build_remote(s3(endpoint="https://s3.example.org"))
  assert remote == ":s3,provider='Other',env_auth='false',endpoint='https://s3.example.org':eawag-lexplore"


def test_build_remote_for_a_switchdrive_share_uses_only_the_token():
  remote = build_remote(switchdrive("Level2"))
  assert remote == ":webdav,url='https://drive.switch.ch/public.php/webdav/',vendor='owncloud',user='aB3dE5fG7hJ9kL1':Level2"


def test_quote_doubles_quotes_so_a_value_cant_add_options():
  assert quote("a',access_key_id='x") == "'a'',access_key_id=''x'"
  assert quote("a,b:c") == "'a,b:c'"


def test_build_remote_escapes_values():
  remote = build_remote(s3(endpoint="https://s3.example.org/a',access_key_id='x"))
  assert remote == ":s3,provider='Other',env_auth='false',endpoint='https://s3.example.org/a'',access_key_id=''x':eawag-lexplore"


def test_rclone_env_leaves_out_credentials(monkeypatch):
  monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAIOSFODNN7EXAMPLE")
  monkeypatch.setenv("RCLONE_S3_ACCESS_KEY_ID", "AKIAIOSFODNN7EXAMPLE")
  assert set(rclone_env()) == {"PATH", "HOME"}


def test_parse_entry_for_a_file():
  line = (
    '{"Path":"2024/a.nc","Name":"a.nc","Size":18342011,"MimeType":"application/x-netcdf",'
    '"ModTime":"2024-06-01T02:12:40.5+02:00","IsDir":false,"Tier":"STANDARD"},'
  )
  entry = parse_entry(line)
  assert entry is not None
  assert entry.path == "2024/a.nc"
  assert entry.size == 18342011
  assert entry.modified_at == datetime(2024, 6, 1, 0, 12, 40, 500000, tzinfo=UTC)


def test_parse_entry_without_modification_time():
  entry = parse_entry('{"Path":"a.nc","Name":"a.nc","Size":0,"MimeType":"application/x-netcdf","IsDir":false}')
  assert entry is not None
  assert entry.size == 0
  assert entry.modified_at is None
