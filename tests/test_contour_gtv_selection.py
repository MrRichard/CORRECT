"""
Tests for target (GTV) ROI selection in ContourProcessor.

Workstation exports always carry a "GTV" ROI, which is the treatment target.
Only GTV ROIs may be rendered: the overlay plane is the union of all GTV masks,
the SC images draw each GTV separately, and a study with no GTV must fail.
"""

import logging
import os
import sys

import numpy as np
import pytest
from pydicom import dcmread
from pydicom.dataset import Dataset, FileDataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, generate_uid

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from netrt_core.contour_processor import ContourProcessor  # noqa: E402
from test_contour_processor_warning import _make_ct_slice  # noqa: E402

RTSTRUCT_SOP_CLASS_UID = "1.2.840.10008.5.1.4.1.1.481.3"
CT_SOP_CLASS_UID = "1.2.840.10008.5.1.4.1.1.2"

# Pixel-space squares (row0, col0, row1, col1) on the first slice, one per ROI.
SQUARES = {
    "GTV1": (2, 2, 8, 8),
    "gtv_boost": (2, 20, 8, 26),
    "BODY": (20, 2, 26, 8),
    "Skull": (20, 20, 26, 26),
}


def _square_contour_data(square, z=0.0, origin=-100.0, spacing=0.7):
    r0, c0, r1, c1 = square
    x0, x1 = origin + c0 * spacing, origin + c1 * spacing
    y0, y1 = origin + r0 * spacing, origin + r1 * spacing
    return [x0, y0, z, x1, y0, z, x1, y1, z, x0, y1, z]


def _make_multi_roi_rtstruct(path, roi_names, ct_uids):
    """RTSTRUCT with one square contour per ROI, all on the first CT slice."""
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

    roi_contours, structure_rois, observations = [], [], []
    for number, name in enumerate(roi_names, start=1):
        contour_image = Dataset()
        contour_image.ReferencedSOPClassUID = CT_SOP_CLASS_UID
        contour_image.ReferencedSOPInstanceUID = ct_uids[0]

        contour = Dataset()
        contour.ContourGeometricType = "CLOSED_PLANAR"
        contour.NumberOfContourPoints = 4
        contour.ContourData = _square_contour_data(SQUARES[name])
        contour.ContourImageSequence = [contour_image]

        roi_contour = Dataset()
        roi_contour.ROIDisplayColor = [255, 0, 0]
        roi_contour.ReferencedROINumber = number
        roi_contour.ContourSequence = [contour]
        roi_contours.append(roi_contour)

        structure_roi = Dataset()
        structure_roi.ROINumber = number
        structure_roi.ROIName = name
        structure_roi.ROIGenerationAlgorithm = "MANUAL"
        structure_rois.append(structure_roi)

        observation = Dataset()
        observation.ObservationNumber = number
        observation.ReferencedROINumber = number
        observation.RTROIInterpretedType = "GTV" if "gtv" in name.lower() else "ORGAN"
        observation.ROIInterpreter = ""
        observations.append(observation)

    ds.ROIContourSequence = roi_contours
    ds.StructureSetROISequence = structure_rois
    ds.RTROIObservationsSequence = observations

    contour_image_items = []
    for sop_uid in ct_uids:
        item = Dataset()
        item.ReferencedSOPClassUID = CT_SOP_CLASS_UID
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


def _make_study(tmp_path, roi_names):
    dcm_dir = tmp_path / "DCM"
    dcm_dir.mkdir()
    uids = []
    for i in range(3):
        uid = f"1.2.826.0.1.3680043.9.7.10.{i}"
        _make_ct_slice(str(dcm_dir / f"slice_{i}.dcm"), uid, i)
        uids.append(uid)
    struct_path = _make_multi_roi_rtstruct(str(tmp_path / "struct.dcm"), roi_names, uids)
    return dcm_dir, struct_path


def _config():
    return {
        "processing": {
            "ignore_contour_names_containing": ["skull"],
            "target_contour_names_containing": ["GTV"],
            "overlay_series_number": 999,
            "sc_series_number": 300,
        },
        "features": {
            "create_augmented_series": True,
            "generate_jpg_visualizations": False,
            "create_secondary_capture": False,
        },
    }


def _load_rt_struct(dcm_dir, struct_path):
    from rt_utils import RTStructBuilder

    return RTStructBuilder.create_from(
        dicom_series_path=str(dcm_dir), rt_struct_path=struct_path, warn_only=True
    )


ALL_ROIS = ["Skull", "BODY", "GTV1", "gtv_boost"]


def test_only_gtv_rois_are_selected(tmp_path):
    dcm_dir, struct_path = _make_study(tmp_path, ALL_ROIS)
    rt_struct = _load_rt_struct(dcm_dir, struct_path)

    processor = ContourProcessor(_config())
    assert processor._get_target_rois(rt_struct) == ["GTV1", "gtv_boost"]


def test_individual_contours_are_gtv_only(tmp_path):
    """SC images draw one colour per entry, so only GTVs may appear here."""
    dcm_dir, struct_path = _make_study(tmp_path, ALL_ROIS)
    rt_struct = _load_rt_struct(dcm_dir, struct_path)

    contours = ContourProcessor(_config())._get_individual_contours(rt_struct)
    assert [c["name"] for c in contours] == ["GTV1", "gtv_boost"]


def test_overlay_plane_is_union_of_gtvs(tmp_path):
    dcm_dir, struct_path = _make_study(tmp_path, ALL_ROIS)
    rt_struct = _load_rt_struct(dcm_dir, struct_path)
    gtv_union = np.logical_or(
        rt_struct.get_roi_mask_by_name("GTV1"), rt_struct.get_roi_mask_by_name("gtv_boost")
    )
    body = rt_struct.get_roi_mask_by_name("BODY")
    assert gtv_union[:, :, 0].any() and body[:, :, 0].any()

    output_path = tmp_path / "Addition"
    success, _ = ContourProcessor(_config()).run(
        str(dcm_dir), struct_path, str(output_path), "1.2.826.0.1.3680043.9.7.1"
    )
    assert success is True

    overlay = dcmread(str(output_path / "OVERLAY-slice_0.dcm")).overlay_array(0x6000).astype(bool)
    np.testing.assert_array_equal(overlay, gtv_union[:, :, 0])
    assert not np.logical_and(overlay, body[:, :, 0]).any()


def test_study_without_gtv_fails(tmp_path, caplog):
    dcm_dir, struct_path = _make_study(tmp_path, ["Skull", "BODY"])
    output_path = tmp_path / "Addition"

    with caplog.at_level(logging.ERROR):
        success, sc_dicom_dir = ContourProcessor(_config()).run(
            str(dcm_dir), struct_path, str(output_path), "1.2.826.0.1.3680043.9.7.1"
        )

    assert (success, sc_dicom_dir) == (False, None)
    assert "No target contours found" in caplog.text
    assert not any(f.startswith("OVERLAY-") for f in os.listdir(output_path))
