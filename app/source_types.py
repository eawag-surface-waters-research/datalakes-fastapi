from app.enums import SourceBackend
from app.schemas import SourceType, SourceTypeOption

# The backends a source can use, with the options each one accepts
SOURCE_TYPES = [
  SourceType(
    backend=SourceBackend.s3,
    name="S3 bucket",
    description="A public bucket on Amazon S3 or an S3-compatible store. The bucket must allow anonymous reads.",
    path_description="Prefix inside the bucket, e.g. `thermistorchain/Level2`. Empty for the whole bucket.",
    options=[
      SourceTypeOption(name="bucket", type="string", required=True, description="Name of the bucket.", example="eawag-lexplore"),
      SourceTypeOption(name="region", type="string", required=False, description="Region of the bucket, for Amazon S3.", example="eu-central-1"),
      SourceTypeOption(
        name="endpoint",
        type="string",
        required=False,
        description="URL of an S3-compatible store. Leave it out for Amazon S3. Must be a public HTTPS address.",
        example="https://s3.example.org",
      ),
    ],
  ),
  SourceType(
    backend=SourceBackend.switchdrive,
    name="SWITCHdrive public share",
    description=(
      "A folder shared by public link on SWITCHdrive. The share must not have a password, and should only contain the dataset's files, "
      "since anyone with the link can read all of it."
    ),
    path_description="Folder inside the share, e.g. `Level2`. Empty for the whole share.",
    options=[
      SourceTypeOption(
        name="share_url",
        type="string",
        required=True,
        description="The public link of the share. Only the share token in it is stored.",
        example="https://drive.switch.ch/index.php/s/aB3dE5fG7hJ9kL1",
      ),
    ],
  ),
]
