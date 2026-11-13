from typing import get_args

import pytest
from pydantic import ValidationError

from app.backends import BACKENDS, BACKENDS_BY_ID, Backend, PublicUrl, S3Options, SwitchdriveOptions, parse_source
from app.enums import SourceBackend
from app.rclone import build_remote


def example_options(backend: type[Backend]) -> dict:
  """Options for a backend made from the first example of each option."""
  return {name: field.examples[0] for name, field in backend.model_fields.items()}


def test_every_backend_id_has_exactly_one_backend():
  assert len(BACKENDS_BY_ID) == len(BACKENDS)
  assert set(BACKENDS_BY_ID) == set(SourceBackend)


@pytest.mark.parametrize("backend", BACKENDS)
def test_backend_has_info(backend):
  assert backend.info.name
  assert backend.info.description


@pytest.mark.parametrize("backend", BACKENDS)
def test_every_option_has_a_description_and_an_example(backend):
  assert backend.model_fields
  for name, field in backend.model_fields.items():
    assert field.description, f"{backend.__name__}.{name} has no description"
    assert field.examples, f"{backend.__name__}.{name} has no examples"


@pytest.mark.parametrize("backend", BACKENDS)
def test_every_option_is_a_string(backend):
  for name, field in backend.model_fields.items():
    types = set(get_args(field.annotation)) - {type(None)} or {field.annotation}
    assert all(isinstance(option_type, type) and issubclass(option_type, str) for option_type in types), f"{backend.__name__}.{name} isn't a string"


@pytest.mark.parametrize("backend", BACKENDS)
def test_backend_rejects_options_not_on_its_allowlist(backend):
  with pytest.raises(ValidationError, match="unknown_option|Unknown option 'password'"):
    backend.model_validate({**example_options(backend), "password": "x"})


# --- S3 ---


def s3(**options) -> str:
  """The rclone remote of an S3 source."""
  return build_remote(parse_source({"backend": "s3", "options": {"bucket": "eawag-lexplore", **options}}))


def test_s3_remote_for_amazon_s3():
  remote = s3(region="eu-central-1", path="thermistorchain/Level2/")
  assert remote == ":s3,provider='AWS',env_auth='false',region='eu-central-1':eawag-lexplore/thermistorchain/Level2"


def test_s3_remote_for_the_whole_bucket():
  assert s3() == ":s3,provider='AWS',env_auth='false':eawag-lexplore"


def test_s3_remote_for_an_s3_compatible_store():
  assert s3(endpoint="https://s3.example.org") == ":s3,provider='Other',env_auth='false',endpoint='https://s3.example.org':eawag-lexplore"


def test_s3_remote_escapes_the_endpoint():
  remote = s3(endpoint="https://s3.example.org/a',access_key_id='x")
  assert remote == ":s3,provider='Other',env_auth='false',endpoint='https://s3.example.org/a'',access_key_id=''x':eawag-lexplore"


def test_s3_endpoint_is_a_public_url():
  options = S3Options.model_validate({"bucket": "eawag-lexplore", "endpoint": "https://s3.example.org"})
  assert options.public_urls() == {"endpoint": "https://s3.example.org"}
  assert isinstance(options.public_urls()["endpoint"], PublicUrl)
  assert S3Options.model_validate({"bucket": "eawag-lexplore"}).public_urls() == {}


def test_s3_error_messages_name_the_bucket():
  options = S3Options.model_validate({"bucket": "eawag-lexplore"})
  assert options.not_found_detail() == "Bucket eawag-lexplore does not exist"
  assert options.not_public_detail().startswith("Access denied to bucket eawag-lexplore.")


# --- SWITCHdrive ---


def test_switchdrive_remote_uses_only_the_token():
  source = parse_source({"backend": "switchdrive", "options": {"share_url": "https://drive.switch.ch/index.php/s/aB3dE5fG7hJ9kL1/"}})
  assert build_remote(source) == ":webdav,url='https://drive.switch.ch/public.php/webdav/',vendor='owncloud',user='aB3dE5fG7hJ9kL1':"


def test_switchdrive_error_messages():
  options = SwitchdriveOptions.model_validate({"share_url": "https://drive.switch.ch/index.php/s/aB3dE5fG7hJ9kL1"})
  assert options.not_found_detail() == "The SWITCHdrive share does not exist"
  assert "password" in options.not_public_detail()
