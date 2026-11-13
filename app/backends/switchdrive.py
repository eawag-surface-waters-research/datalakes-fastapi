import re

from pydantic import Field, field_validator

from app.backends.base import Backend, BackendInfo, RcloneRemote
from app.enums import SourceBackend
from app.errors import invalid

SHARE_URL_PATTERN = re.compile(r"^https://drive\.switch\.ch/index\.php/s/(?P<token>[A-Za-z0-9]+)/?$")
WEBDAV_URL = "https://drive.switch.ch/public.php/webdav/"


class SwitchdriveOptions(Backend):
  """Options for a SWITCHdrive source. Only these are accepted."""

  info = BackendInfo(
    id=SourceBackend.switchdrive,
    name="SWITCHdrive public share",
    description=(
      "A folder shared by public link on SWITCHdrive. The share must not have a password, "
      "and must hold the dataset's files directly, without subfolders."
    ),
  )

  share_url: str = Field(
    description="The public link of the share. Password-protected shares are rejected.",
    examples=["https://drive.switch.ch/index.php/s/aB3dE5fG7hJ9kL1"],
  )

  @field_validator("share_url")
  @classmethod
  def must_be_a_share_link(cls, share_url: str) -> str:
    if not SHARE_URL_PATTERN.match(share_url):
      raise invalid("Must be a SWITCHdrive public link, e.g. https://drive.switch.ch/index.php/s/aB3dE5fG7hJ9kL1")
    return share_url

  @property
  def share_token(self) -> str:
    match = SHARE_URL_PATTERN.match(self.share_url)
    assert match  # to satisfy type checker
    return match["token"]

  def rclone_remote(self) -> RcloneRemote:
    # The share token is the WebDAV user name of a public share
    return RcloneRemote(type="webdav", params={"url": WEBDAV_URL, "vendor": "owncloud", "user": self.share_token}, root="")

  def not_found_detail(self) -> str:
    return "The SWITCHdrive share does not exist"

  def has_folders_hint(self) -> str:
    return "Share a folder that holds the files directly, without subfolders, or move the subfolders out of the share."

  def not_public_detail(self) -> str:
    # SWITCHdrive answers 401 both for a wrong link and for a share with a password
    return "The SWITCHdrive share can't be read anonymously. Check that the link is right and that the share has no password."
