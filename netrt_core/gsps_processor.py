import os
import logging
import datetime
import pydicom
from pydicom import FileDataset, FileMetaDataset, Sequence, Dataset
from pydicom.uid import generate_uid, ExplicitVRLittleEndian
from pydicom.tag import Tag

logger = logging.getLogger(__name__)


class GSPSProcessor:
    """Creates a Grayscale Softcopy Presentation State (GSPS) for overlay series."""

    def __init__(self, config):
        self.config = config

    def create_gsps(self, overlay_dicom_files: list, output_dir: str) -> str:
        """
        Creates a GSPS DICOM file referencing the overlay series.

        Args:
            overlay_dicom_files: List of pydicom Datasets from the overlay series.
            output_dir: Directory where the GSPS .dcm file will be saved.

        Returns:
            str: Path to the created GSPS file.
        """
        if not overlay_dicom_files:
            raise ValueError("No overlay DICOM files provided for GSPS creation.")

        ref = overlay_dicom_files[0]
        sop_instance_uid = generate_uid()

        output_path = os.path.join(output_dir, f"GSPS_{sop_instance_uid}.dcm")

        file_meta = FileMetaDataset()
        file_meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.11.1"
        file_meta.MediaStorageSOPInstanceUID = sop_instance_uid
        file_meta.TransferSyntaxUID = ExplicitVRLittleEndian

        gsps = FileDataset(output_path, {}, file_meta=file_meta, preamble=b"\0" * 128)

        # SOP Common Module
        gsps.SOPClassUID = "1.2.840.10008.5.1.4.1.1.11.1"
        gsps.SOPInstanceUID = sop_instance_uid

        # Patient Module
        for tag_name in ["PatientName", "PatientID", "PatientBirthDate", "PatientSex"]:
            if hasattr(ref, tag_name):
                setattr(gsps, tag_name, getattr(ref, tag_name))

        # General Study Module
        for tag_name in [
            "StudyInstanceUID", "StudyDate", "StudyTime",
            "ReferringPhysicianName", "StudyID", "AccessionNumber", "StudyDescription",
        ]:
            if hasattr(ref, tag_name):
                setattr(gsps, tag_name, getattr(ref, tag_name))

        # General Series Module
        gsps.Modality = "PR"
        gsps.SeriesInstanceUID = generate_uid()
        gsps.SeriesNumber = self.config.get("processing", {}).get("gsps_series_number", 100)

        # Presentation State Module
        now = datetime.datetime.now()
        gsps.PresentationCreationDate = now.strftime("%Y%m%d")
        gsps.PresentationCreationTime = now.strftime("%H%M%S.%f")
        gsps.ContentLabel = "OVERLAY"
        gsps.ContentDescription = "Contour Overlay Activation"
        gsps.ContentCreatorName = "CORRECT"
        gsps.InstanceNumber = 1
        gsps.SeriesDate = now.strftime("%Y%m%d")
        gsps.SeriesTime = now.strftime("%H%M%S.%f")

        # Referenced Series Sequence — group overlay instances by series UID
        series_map = {}
        for ds in overlay_dicom_files:
            series_uid = str(ds.SeriesInstanceUID)
            if series_uid not in series_map:
                series_map[series_uid] = []
            series_map[series_uid].append(ds)

        ref_series_seq = []
        for series_uid, series_datasets in series_map.items():
            series_item = Dataset()
            series_item.SeriesInstanceUID = series_uid

            ref_img_seq = []
            for ds in series_datasets:
                img_item = Dataset()
                img_item.ReferencedSOPClassUID = str(ds.SOPClassUID)
                img_item.ReferencedSOPInstanceUID = str(ds.SOPInstanceUID)
                ref_img_seq.append(img_item)

            series_item.ReferencedImageSequence = Sequence(ref_img_seq)
            ref_series_seq.append(series_item)

        gsps.ReferencedSeriesSequence = Sequence(ref_series_seq)

        # Graphic Layer Sequence — required when activating overlays.
        # GraphicLayerRecommendedDisplayCIELabValue tells the viewer what colour to render the
        # overlay with.  Values are DICOM-scaled CIELab (3 × uint16, 0-65535):
        #   L_dicom = L*  × 65535 / 100
        #   a_dicom = (a* + 128) × 65535 / 255
        #   b_dicom = (b* + 128) × 65535 / 255
        # Default: pure red  → CIELab (53.23, 80.11, 67.22) → DICOM [34895, 53534, 50196].
        # Note: opacity/transparency is NOT standardised in the Grayscale Softcopy PS SOP class;
        # how (and whether) the overlay blends with the image is viewer-dependent.
        overlay_cielab = self.config.get("processing", {}).get(
            "gsps_overlay_color_cielab", [34895, 53534, 50196]
        )
        layer_item = Dataset()
        layer_item.GraphicLayerName = "OVERLAY_LAYER"
        layer_item.GraphicLayerOrder = 1
        layer_item.GraphicLayerDescription = "Contour Overlay"
        layer_item.GraphicLayerRecommendedDisplayCIELabValue = overlay_cielab
        gsps.GraphicLayerSequence = Sequence([layer_item])

        # Overlay Activation Module — activate group 0x6000
        # (6000,1500) OverlayLabel (retired CS tag, identifies the overlay group)
        gsps.add_new(Tag(0x6000, 0x1500), "CS", "OVERLAY_0")
        # (6000,3000) OverlayActivationLayer (LO in GSPS context) — maps group to graphic layer
        gsps.add_new(Tag(0x6000, 0x3000), "LO", "OVERLAY_LAYER")

        # Softcopy VOI LUT Sequence — carry window/level from overlay images
        window_center, window_width = self._find_window_settings(overlay_dicom_files)
        if window_center is not None and window_width is not None:
            voi_item = Dataset()

            voi_ref_seq = []
            for ds in overlay_dicom_files:
                voi_ref_item = Dataset()
                voi_ref_item.ReferencedSOPClassUID = str(ds.SOPClassUID)
                voi_ref_item.ReferencedSOPInstanceUID = str(ds.SOPInstanceUID)
                voi_ref_seq.append(voi_ref_item)
            voi_item.ReferencedImageSequence = Sequence(voi_ref_seq)
            voi_item.WindowCenter = window_center
            voi_item.WindowWidth = window_width

            gsps.SoftcopyVOILUTSequence = Sequence([voi_item])

        pydicom.dcmwrite(output_path, gsps)
        logger.info(f"GSPS file created: {output_path}")
        return output_path

    def _find_window_settings(self, datasets: list):
        """Returns the first valid (WindowCenter, WindowWidth) pair found in datasets."""
        for ds in datasets:
            if hasattr(ds, "WindowCenter") and hasattr(ds, "WindowWidth"):
                try:
                    wc = ds.WindowCenter
                    ww = ds.WindowWidth
                    # Handle multi-value VR (pydicom DSfloat sequences)
                    if hasattr(wc, "__iter__") and not isinstance(wc, (str, bytes)):
                        wc = float(list(wc)[0])
                        ww = float(list(ww)[0])
                    else:
                        wc = float(wc)
                        ww = float(ww)
                    return wc, ww
                except (TypeError, ValueError, IndexError):
                    continue
        return None, None
