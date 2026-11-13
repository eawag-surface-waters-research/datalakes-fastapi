from enum import StrEnum


class DatasetStatus(StrEnum):
  published = "published"
  draft = "draft"


class DatasetSort(StrEnum):
  title = "title"
  recent = "recent"
  downloads = "downloads"


class DatasetKind(StrEnum):
  data = "data"
  link = "link"


class FeatureType(StrEnum):
  time_series = "timeSeries"
  time_series_profile = "timeSeriesProfile"
  profile = "profile"


class VariableRole(StrEnum):
  time = "time"
  coordinate = "coordinate"
  data = "data"
  flag = "flag"


class IssueCategory(StrEnum):
  problem = "problem"
  event = "event"


class IssueState(StrEnum):
  open = "open"
  resolved = "resolved"


class SourceBackend(StrEnum):
  s3 = "s3"
  switchdrive = "switchdrive"


class JobType(StrEnum):
  fetch = "fetch"
  sync = "sync"
  rebuild = "rebuild"
  export = "export"
  cleanup = "cleanup"
  test = "test"


class JobStatus(StrEnum):
  queued = "queued"
  running = "running"
  succeeded = "succeeded"
  failed = "failed"
  cancelled = "cancelled"


class FileStatus(StrEnum):
  new = "new"
  ok = "ok"
  failed = "failed"


class ExportFormat(StrEnum):
  netcdf = "netcdf"
  csv = "csv"


class ExportStatus(StrEnum):
  queued = "queued"
  running = "running"
  ready = "ready"
  failed = "failed"
  expired = "expired"


class RegenerateRunStatus(StrEnum):
  running = "running"
  finished = "finished"
  stopped = "stopped"
