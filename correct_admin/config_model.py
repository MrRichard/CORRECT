"""Config file model for the graphical editor (no Tk dependency, so it is unit-testable).

Loads config.yaml with ruamel.yaml in round-trip mode so comments, key order and
quoting survive a save. Fields that are absent from the file show the runtime
default from netrt_core.config_loader.DEFAULT_CONFIG and are only written when the
user changes them.
"""

import ipaddress
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.scalarstring import ScalarString

from netrt_core.config_loader import DEFAULT_CONFIG
from netrt_core.platform_paths import default_config_path

LOG_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

_HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*\.?$"
)


@dataclass
class Field:
    """One editable setting. ``path`` is a tuple of keys into the YAML document."""

    path: tuple
    label: str
    kind: str  # str | int | port | aet | host | bool | dir | file | list | choice
    help: str = ""
    choices: list = field(default_factory=list)
    required: bool = True

    @property
    def key(self):
        return ".".join(self.path)


# Tab name -> fields shown on that tab, in display order.
SECTIONS = {
    "Listener": [
        Field(("dicom_listener", "host"), "Listen address", "host", "0.0.0.0 listens on all interfaces"),
        Field(("dicom_listener", "port"), "Listen port", "port"),
        Field(("dicom_listener", "ae_title"), "AE title", "aet"),
        Field(("dicom_listener", "config_negotiated_transfer_syntax"), "Save using negotiated transfer syntax", "bool"),
    ],
    "Destination": [
        Field(("dicom_destination", "ip"), "Destination host / IP", "host"),
        Field(("dicom_destination", "port"), "Destination port", "port"),
        Field(("dicom_destination", "ae_title"), "Destination AE title", "aet"),
    ],
    "Directories": [
        Field(("directories", "working"), "Working directory", "dir", "Incoming studies (fast local disk)"),
        Field(("directories", "logs"), "Log directory", "dir"),
        Field(("directories", "quarantine_subdir"), "Quarantine subfolder name", "str"),
        Field(("reporting", "output_directory"), "Report directory", "dir"),
        Field(("anonymization", "pid_mapping_file"), "PID mapping file", "file"),
    ],
    "Anonymization": [
        Field(("anonymization", "apply_site_code"), "Replace PatientID/Name with site-coded ID", "bool"),
        Field(("anonymization", "apply_anonymization_rules"), "Apply tag removal/blanking rules", "bool"),
        Field(("anonymization", "site_code"), "Site code", "str"),
        Field(("anonymization", "study_description"), "Study description", "str", required=False),
        Field(("anonymization", "rules", "remove_tags"), "Tags to remove (one per line)", "list", required=False),
        Field(("anonymization", "rules", "blank_tags"), "Tags to blank (one per line)", "list", required=False),
    ],
    "Features": [
        Field(("features", "send_original_series"), "Send original series", "bool"),
        Field(("features", "create_augmented_series"), "Create augmented overlay series", "bool"),
        Field(("features", "create_gsps"), "Create GSPS", "bool"),
        Field(("features", "create_segmentation_export"), "Create DICOM SEG export", "bool"),
        Field(("features", "create_secondary_capture"), "Create secondary capture", "bool"),
        Field(("features", "generate_jpg_visualizations"), "Generate JPG visualizations", "bool"),
    ],
    "Processing": [
        Field(("processing", "target_contour_names_containing"), "Target contour name patterns (one per line)", "list"),
        Field(("processing", "ignore_contour_names_containing"), "Ignored contour name patterns (one per line)", "list", required=False),
        Field(("processing", "add_burn_in_disclaimer"), "Burn in disclaimer text", "bool"),
        Field(("processing", "burn_in_text"), "Burn-in text", "str", required=False),
        Field(("processing", "overlay_series_number"), "Overlay series number", "int"),
        Field(("processing", "overlay_series_description"), "Overlay series description", "str", required=False),
        Field(("processing", "gsps_series_number"), "GSPS series number", "int"),
        Field(("processing", "segmentation_series_number"), "SEG series number", "int"),
        Field(("processing", "sc_series_number"), "SC series number", "int"),
        Field(("processing", "sc_series_description"), "SC series description", "str", required=False),
        Field(("watcher", "debounce_interval_seconds"), "Study completion wait (seconds)", "int"),
        Field(("watcher", "min_file_count_for_processing"), "Minimum files per study", "int"),
    ],
    "Logging": [
        Field(("logging", "level"), "Log level", "choice", choices=LOG_LEVELS),
    ],
}


def all_fields():
    for fields in SECTIONS.values():
        yield from fields


def resolve_config_path(cli_path=None):
    """--config, then $CORRECT_CONFIG, then the platform default."""
    return cli_path or os.environ.get("CORRECT_CONFIG") or default_config_path()


def _get_path(data, path):
    cur = data
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return None, False
        cur = cur[key]
    return cur, True


class ConfigModel:
    """A loaded config document plus typed get/set and validation."""

    def __init__(self, path):
        self.path = path
        self._yaml = YAML()  # round-trip
        self._yaml.preserve_quotes = True
        self._yaml.indent(mapping=2, sequence=4, offset=2)
        self._yaml.width = 4096
        self.data = CommentedMap()
        self.exists = False
        self.load()

    # ── I/O ────────────────────────────────────────────────────────────────
    def load(self):
        if os.path.exists(self.path):
            with open(self.path, "r", encoding="utf-8") as f:
                loaded = self._yaml.load(f)
            self.data = loaded if isinstance(loaded, CommentedMap) else CommentedMap()
            self.exists = True
        else:
            self.data = CommentedMap()
            self.exists = False

    def save(self, backup=True):
        """Validate, back up the previous file to <name>.bak and write atomically."""
        errors = self.validate()
        if errors:
            raise ValueError("; ".join(f"{k}: {v}" for k, v in errors.items()))

        directory = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(directory, exist_ok=True)
        st = None
        if os.path.exists(self.path):
            st = os.stat(self.path)
            if backup:
                shutil.copy2(self.path, self.path + ".bak")

        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".config-", suffix=".yaml")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                self._yaml.dump(self.data, f)
            if st is not None:
                # Keep the original mode and (on POSIX, when we can) owner, e.g. 0640 correct:correct.
                os.chmod(tmp, st.st_mode & 0o7777)
                if hasattr(os, "chown"):
                    try:
                        os.chown(tmp, st.st_uid, st.st_gid)
                    except OSError:
                        pass
            os.replace(tmp, self.path)
        except BaseException:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise
        self.exists = True

    # ── values ─────────────────────────────────────────────────────────────
    def get(self, fld):
        """Current value (from the file, else the runtime default), normalised for display."""
        value, found = _get_path(self.data, fld.path)
        if not found:
            value, _ = _get_path(DEFAULT_CONFIG, fld.path)
        if fld.kind == "list":
            if value is None:
                return []
            if isinstance(value, str):
                return [value]
            return [str(v) for v in value]
        if fld.kind == "bool":
            return bool(value)
        return "" if value is None else value

    def set(self, fld, value):
        """Set a value from the UI. Values are coerced to the field's type when possible."""
        value = self._coerce(fld, value)
        _, found = _get_path(self.data, fld.path)
        if not found and value == self.get(fld):
            return  # still the runtime default — don't add the key to the file
        cur = self.data
        for key in fld.path[:-1]:
            if key not in cur or not isinstance(cur[key], dict):
                cur[key] = CommentedMap()
            cur = cur[key]
        last = fld.path[-1]
        existing = cur.get(last)
        if fld.kind == "list" and isinstance(existing, CommentedSeq):
            if [str(v) for v in existing] == value:
                return  # unchanged — keep original quoting
            # Mutate in place so flow style (["GTV"]) and comments are kept, and
            # reuse the quoting style of the first existing item.
            style = type(existing[0]) if existing and isinstance(existing[0], ScalarString) else None
            del existing[:]
            existing.extend(style(v) if style else v for v in value)
        elif existing == value and isinstance(existing, bool) == isinstance(value, bool):
            return  # unchanged
        elif isinstance(existing, ScalarString) and isinstance(value, str):
            cur[last] = type(existing)(value)  # keep "quoted" / 'quoted' style
        else:
            cur[last] = value

    @staticmethod
    def _coerce(fld, value):
        if fld.kind == "bool":
            return bool(value)
        if fld.kind in ("int", "port"):
            if isinstance(value, int) and not isinstance(value, bool):
                return value
            text = str(value).strip()
            try:
                return int(text)
            except ValueError:
                return text  # validate() reports it
        if fld.kind == "list":
            if isinstance(value, str):
                value = value.splitlines()
            return [str(v).strip() for v in value if str(v).strip()]
        return "" if value is None else str(value).strip()

    # ── validation ─────────────────────────────────────────────────────────
    def validate(self):
        """Return {field_key: message} for every invalid field (empty dict when valid)."""
        errors = {}
        for fld in all_fields():
            msg = validate_value(fld, self.get(fld))
            if msg:
                errors[fld.key] = msg
        return errors


def validate_value(fld, value):
    """Return an error message, or None if ``value`` is acceptable for ``fld``."""
    kind = fld.kind
    if kind == "bool":
        return None
    if kind == "list":
        if fld.required and not value:
            return "at least one entry is required"
        return None
    if kind in ("int", "port"):
        if isinstance(value, bool) or not isinstance(value, int):
            try:
                value = int(str(value).strip())
            except ValueError:
                return "must be a whole number"
        if kind == "port" and not (1 <= value <= 65535):
            return "must be between 1 and 65535"
        if kind == "int" and value < 0:
            return "must not be negative"
        return None

    text = "" if value is None else str(value).strip()
    if not text:
        return "is required" if fld.required else None
    if kind == "aet":
        if len(text) > 16:
            return "AE titles are limited to 16 characters"
        if "\\" in text or any(ord(c) < 32 for c in text):
            return "contains characters not allowed in an AE title"
    elif kind == "host":
        try:
            ipaddress.ip_address(text)
        except ValueError:
            if not _HOSTNAME_RE.match(text):
                return "must be an IP address or hostname"
    elif kind in ("dir", "file"):
        if not os.path.isabs(os.path.expanduser(text)):
            return "must be an absolute path"
    elif kind == "choice":
        if fld.choices and text.upper() not in fld.choices:
            return f"must be one of {', '.join(fld.choices)}"
    return None
