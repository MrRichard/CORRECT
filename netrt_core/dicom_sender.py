import os
import logging
from pydicom import dcmread
from pynetdicom import AE
from pynetdicom.sop_class import (
    CTImageStorage,
    MRImageStorage,
    RTStructureSetStorage,
    SegmentationStorage,
    SecondaryCaptureImageStorage,
    GrayscaleSoftcopyPresentationStateStorage,
)
from pydicom.uid import (
    ImplicitVRLittleEndian,
    ExplicitVRLittleEndian,
    ExplicitVRBigEndian,
    JPEGBaseline8Bit,
    JPEGExtended12Bit,
    JPEG2000Lossless,
    JPEG2000
)

logger = logging.getLogger(__name__)

class DicomSender:
    """A class to handle sending DICOM files to a remote AE."""

    def __init__(self, host, port, ae_title):
        """
        Initializes the DicomSender.

        Args:
            host (str): The IP address of the destination.
            port (int): The port number of the destination.
            ae_title (str): The AE Title of the destination.
        """
        self.host = host
        self.port = port
        self.ae_title = ae_title
        self.ae = AE()
        self._configure_contexts()

    def _configure_contexts(self):
        """Configure a reasonable set of presentation contexts to avoid exceeding limits."""
        # Common transfer syntaxes that should cover most use cases
        common_transfer_syntaxes = [
            ImplicitVRLittleEndian,
            ExplicitVRLittleEndian,
            ExplicitVRBigEndian,
            JPEGBaseline8Bit,
            JPEG2000Lossless
        ]
        
        # Common storage SOP classes that CORRECT is likely to work with
        common_storage_classes = [
            CTImageStorage,
            MRImageStorage,
            RTStructureSetStorage,
            SegmentationStorage,
            SecondaryCaptureImageStorage,
            GrayscaleSoftcopyPresentationStateStorage,
        ]
        
        # Add contexts for common combinations
        for sop_class in common_storage_classes:
            self.ae.add_requested_context(sop_class, common_transfer_syntaxes)
        
        logger.debug(f"Configured {len(common_storage_classes)} storage classes with {len(common_transfer_syntaxes)} transfer syntaxes each")

    def send_directory(self, directory_path):
        """
        Sends all DICOM files in a given directory.

        Args:
            directory_path (str): The absolute path to the directory containing DICOM files.
        
        Returns:
            bool: True if all files were sent successfully, False otherwise.
        """
        logger.info(f"Attempting to send files from {directory_path} to {self.ae_title}@{self.host}:{self.port}")
        
        if not os.path.isdir(directory_path):
            logger.error(f"Directory not found: {directory_path}")
            return False

        dicom_files = [os.path.join(directory_path, f) for f in os.listdir(directory_path) if f.lower().endswith('.dcm')]
        if not dicom_files:
            logger.warning(f"No DICOM files found in {directory_path} to send.")
            return True # No files to send is not a failure

        assoc = self.ae.associate(self.host, self.port, ae_title=self.ae_title)

        if not assoc.is_established:
            logger.error(f"Association rejected, aborted or never connected to {self.ae_title}@{self.host}:{self.port}")
            return False

        success = True
        try:
            for filepath in dicom_files:
                try:
                    ds = dcmread(filepath)
                    status = assoc.send_c_store(ds)
                    if status and status.Status == 0x0000:
                        logger.debug(f"Successfully sent {os.path.basename(filepath)}")
                    else:
                        logger.error(f"Failed to send {os.path.basename(filepath)}. Status: {status}")
                        success = False
                except Exception as e:
                    logger.error(f"Exception while sending file {os.path.basename(filepath)}: {e}", exc_info=True)
                    success = False
        finally:
            assoc.release()
            logger.info("Association released.")
        
        return success