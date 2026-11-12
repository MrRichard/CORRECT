import os
import datetime
import traceback

from .platform_paths import default_shared_dir

class ReportGenerator:
    """Generates a processing report for a DICOM study."""

    def __init__(self, config, study_instance_uid, sender_info):
        self.config = config
        self.study_instance_uid = study_instance_uid
        self.sender_info = sender_info
        self.report_path = self._get_report_path()
        self.report_content = []
        self._initialize_report()

    def _get_report_path(self):
        """Gets the full path for the report file."""
        report_dir = self.config.get("reporting", {}).get("output_directory") or default_shared_dir()

        os.makedirs(report_dir, exist_ok=True)
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"report_{self.study_instance_uid}_{timestamp}.txt"
        return os.path.join(report_dir, filename)

    def _initialize_report(self):
        """Initializes the report with basic information."""
        self.add_line("Processing Report")
        self.add_line("=" * 20)
        self.add_line(f"Study Instance UID: {self.study_instance_uid}")
        self.add_line(f"Report Generated: {datetime.datetime.now()}")
        self.add_line(f"Sender IP: {self.sender_info.get('ip', 'N/A')}")
        self.add_line(f"Sender AE Title: {self.sender_info.get('ae_title', 'N/A')}")
        self.add_line("-" * 20)

    def add_line(self, line):
        """Adds a line to the report."""
        self.report_content.append(line)

    def add_error(self, exception):
        """Adds an error to the report."""
        self.add_line("\nERROR")
        self.add_line("=" * 20)
        self.add_line(str(exception))
        self.add_line("-" * 20)
        self.add_line("Stack Trace:")
        self.add_line(traceback.format_exc())

    def write_report(self):
        """Writes the report to a file."""
        with open(self.report_path, "w") as f:
            for line in self.report_content:
                f.write(line + "\n")

