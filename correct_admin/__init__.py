"""Administration tools for CORRECT: config editor, service control, Windows service host."""

SERVICE_NAME = "CORRECT"
SERVICE_DISPLAY_NAME = "CORRECT DICOM RT Structure Processor"
SERVICE_DESCRIPTION = (
    "Receives DICOM studies, processes RT Structure Sets into overlay/GSPS/SEG series "
    "and forwards them to the configured destination."
)
LINUX_UNIT_NAME = "correct"
