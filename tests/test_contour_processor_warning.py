"""
End-to-end test for the production path described in issue #1.

An RTSTRUCT that references a CT slice which is not present in the DCM
directory must not abort contour processing. Before the fix, rt-utils raised
`NameError: name 'warnings' is not defined` on its warn_only path, the study
was quarantined and nothing was sent to the PACS.

This exercises the real ContourProcessor.run() entry point.
"""

import os
import sys

import numpy as np
import pytest
from pydicom.dataset import Dataset, FileDataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, generate_uid

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from netrt_core.contour_processor import ContourProcessor  # noqa: E402

RTSTRUCT_SOP_CLASS_UID = "1.2.840.10008.5.1.4.1.1.481.3"


def _make_ct_slice(path, sop_instance_uid, z_position, rows=32, cols=32):
    meta = FileMetaDataset()
    meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.2"
    meta.MediaStorageSOPInstanceUID = sop_instance_uid
    meta.TransferSyntaxUID = ExplicitVRLittleEndian

    ds = FileDataset(path, {}, file_meta=meta, preamble=b"\0" * 128)
    ds.SOPClassUID = meta.MediaStorageSOPClassUID
    ds.SOPInstanceUID = sop_instance_uid
    ds.StudyInstanceUID = "1.2.826.0.1.3680043.9.7.1"
    ds.SeriesInstanceUID = "1.2.826.0.1.3680043.9.7.2"
    ds.Modality = "CT"
    ds.PatientID = "TESTPAT"
    ds.PatientName = "TEST^PATIENT"
    ds.StudyDate = "20260101"
    ds.StudyTime = "120000"
    ds.SeriesNumber = 1
    ds.InstanceNumber = int(z_position) + 1
    ds.Rows = rows
    ds.Columns = cols
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.BitsAllocated = 16
    ds.BitsStored = 16
    ds.HighBit = 15
    ds.PixelRepresentation = 0
    ds.PixelSpacing = [0.7, 0.7]
    ds.SliceThickness = 2.5
    ds.ImagePositionPatient = [-100.0, -100.0, float(z_position) * 2.5]
    ds.SliceLocation = float(z_position) * 2.5
    ds.ImageOrientationPatient = [1, 0, 0, 0, 1, 0]
    ds.FrameOfReferenceUID = "1.2.826.0.1.3680043.9.7.3"

    # A non-uniform, non-zero image so overlays are visibly meaningful.
    pixels = np.full((rows, cols), 100, dtype=np.uint16)
    pixels[8:24, 8:24] = 900
    ds.PixelData = pixels.tobytes()
    ds.save_as(path, enforce_file_format=True)
    return sop_instance_uid


def _make_rtstruct(path, referenced_sop_instance_uids):
    """RTSTRUCT with a real contour on the first referenced slice."""
    meta = FileMetaDataset()
    meta.MediaStorageSOPClassUID = RTSTRUCT_SOP_CLASS_UID
    meta.MediaStorageSOPInstanceUID = generate_uid()
    meta.TransferSyntaxUID = ExplicitVRLittleEndian

    ds = FileDataset(path, {}, file_meta=meta, preamble=b"\0" * 128)
    ds.SOPClassUID = RTSTRUCT_SOP_CLASS_UID
    ds.SOPInstanceUID = meta.MediaStorageSOPInstanceUID
    ds.StudyInstanceUID = "1.2.826.0.1.3680043.9.7.1"
    ds.SeriesInstanceUID = "1.2.826.0.1.3680043.9.7.4"
    ds.Modality = "RTSTRUCT"
    ds.StructureSetLabel = "TEST"
    ds.StructureSetName = "TEST"
    ds.StructureSetDate = "20260101"
    ds.StructureSetTime = "120000"
    ds.PatientID = "TESTPAT"
    ds.PatientName = "TEST^PATIENT"
    ds.FrameOfReferenceUID = "1.2.826.0.1.3680043.9.7.3"
    ds.ImageOrientationPatient = [1, 0, 0, 0, 1, 0]
    ds.ImagePositionPatient = [-100.0, -100.0, 0.0]

    # --- ROIContourSequence: one ROI with a square contour ---
    contour_image = Dataset()
    contour_image.ReferencedSOPClassUID = "1.2.840.10008.5.1.4.1.1.2"
    contour_image.ReferencedSOPInstanceUID = referenced_sop_instance_uids[0]

    contour = Dataset()
    contour.ContourGeometricType = "CLOSED_PLANAR"
    contour.NumberOfContourPoints = 4
    contour.ContourData = [-90.0, -90.0, 0.0, -60.0, -90.0, 0.0,
                           -60.0, -60.0, 0.0, -90.0, -60.0, 0.0]
    contour.ContourImageSequence = [contour_image]

    roi_contour = Dataset()
    roi_contour.ROINumber = 1
    roi_contour.ROIDisplayColor = [255, 0, 0]
    roi_contour.ReferencedROINumber = 1
    roi_contour.ContourSequence = [contour]
    ds.ROIContourSequence = [roi_contour]

    # --- StructureSetROISequence ---
    structure_roi = Dataset()
    structure_roi.ROINumber = 1
    structure_roi.ROIName = "BODY"
    structure_roi.ROIGenerationAlgorithm = "MANUAL"
    ds.StructureSetROISequence = [structure_roi]

    # --- RTROIObservationsSequence ---
    observation = Dataset()
    observation.ObservationNumber = 1
    observation.ReferencedROINumber = 1
    observation.RTROIInterpretedType = "ORGAN"
    observation.ROIInterpreter = ""
    ds.RTROIObservationsSequence = [observation]

    # --- ReferencedFrameOfReferenceSequence ---
    contour_image_items = []
    for sop_uid in referenced_sop_instance_uids:
        item = Dataset()
        item.ReferencedSOPClassUID = "1.2.840.10008.5.1.4.1.1.2"
        item.ReferencedSOPInstanceUID = sop_uid
        contour_image_items.append(item)

    referenced_series = Dataset()
    referenced_series.SeriesInstanceUID = "1.2.826.0.1.3680043.9.7.2"
    referenced_series.ContourImageSequence = contour_image_items

    referenced_study = Dataset()
    referenced_study.ReferencedSOPClassUID = "1.2.840.10008.3.1.2.3.1"
    referenced_study.ReferencedSOPInstanceUID = "1.2.826.0.1.3680043.9.7.2"
    referenced_study.RTReferencedSeriesSequence = [referenced_series]

    frame_of_reference = Dataset()
    frame_of_reference.FrameOfReferenceUID = "1.2.826.0.1.3680043.9.7.3"
    frame_of_reference.RTReferencedStudySequence = [referenced_study]

    ds.ReferencedFrameOfReferenceSequence = [frame_of_reference]

    ds.save_as(path, enforce_file_format=True)
    return path


@pytest.fixture
def study(tmp_path):
    """DCM dir with three CT slices + an RTSTRUCT referencing a 4th, absent slice."""
    dcm_dir = tmp_path / "DCM"
    dcm_dir.mkdir()
    uids = []
    for i in range(3):
        uid = f"1.2.826.0.1.3680043.9.7.10.{i}"
        _make_ct_slice(str(dcm_dir / f"slice_{i}.dcm"), uid, i)
        uids.append(uid)

    missing_uid = "1.2.826.0.1.3680043.9.7.99"  # referenced but NOT present
    struct_path = _make_rtstruct(
        str(tmp_path / "struct.dcm"), uids + [missing_uid]
    )
    return dcm_dir, struct_path


def _config(tmp_path):
    return {
        "processing": {
            "ignore_contour_names_containing": ["skull"],
            "overlay_series_number": 999,
            "sc_series_number": 300,
        },
        "features": {
            "create_augmented_series": True,
            "generate_jpg_visualizations": False,
            "create_secondary_capture": False,
        },
    }


def test_contour_processing_survives_dangling_rtstruct_reference(study, tmp_path):
    """
    The exact scenario from issue #1: a dangling RTSTRUCT reference must be a
    warning, not a crash. Contour processing must succeed and produce the
    overlay series.
    """
    dcm_dir, struct_path = study
    output_path = tmp_path / "Addition"

    processor = ContourProcessor(_config(tmp_path))
    success, sc_dicom_dir = processor.run(
        str(dcm_dir), struct_path, str(output_path), "1.2.826.0.1.3680043.9.7.1"
    )

    assert success is True, "Contour processing must not fail on a dangling reference"
    assert sc_dicom_dir is None

    # The overlay series must actually have been produced.
    overlays = [f for f in os.listdir(output_path) if f.startswith("OVERLAY-")]
    assert len(overlays) == 3, f"expected 3 overlay files, got {overlays}"


def test_contour_processing_logs_the_warning(study, tmp_path, caplog):
    """The warning must be visible in the application log, not swallowed."""
    import logging

    # Mirror what netrt_core.logging_setup.setup_logging() does in production:
    # route warnings.warn() into the logging system.
    logging.captureWarnings(True)

    dcm_dir, struct_path = study
    output_path = tmp_path / "Addition"

    processor = ContourProcessor(_config(tmp_path))
    with caplog.at_level(logging.WARNING):
        success, _ = processor.run(
            str(dcm_dir), struct_path, str(output_path), "1.2.826.0.1.3680043.9.7.1"
        )

    assert success is True
    messages = "\n".join(r.getMessage() for r in caplog.records)
    assert "not contained in input series data" in messages
