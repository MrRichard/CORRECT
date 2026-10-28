"""
Tests for the rt-utils warning shim (netrt_core/rt_utils_compat.py) and for the
RTSTRUCT series-reference warning path it restores.

Background: rt-utils 1.2.7 ships rt_utils/rtstruct_builder.py without
"import warnings", so its warn_only path raises
`NameError: name 'warnings' is not defined` instead of warning.
Upstream fixed this in qurit/rt-utils commit b70ad40b (2023-01-25) but never
released it, so the published package still carries the bug.
"""

import os
import sys
import warnings

import numpy as np
import pytest
from pydicom.dataset import Dataset, FileDataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, generate_uid

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from netrt_core.rt_utils_compat import (  # noqa: E402
    patch_rt_utils_compat_bootstrap,
    patch_rt_utils_warnings,
)

RTSTRUCT_SOP_CLASS_UID = "1.2.840.10008.5.1.4.1.1.481.3"


# ---------------------------------------------------------------- helpers

def _make_ct_slice(path, sop_instance_uid, z_position, rows=16, cols=16):
    """Write a minimal but valid CT slice that rt-utils can load."""
    meta = FileMetaDataset()
    meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.2"  # CT Image Storage
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
    ds.ImageOrientationPatient = [1, 0, 0, 0, 1, 0]
    ds.FrameOfReferenceUID = "1.2.826.0.1.3680043.9.7.3"

    ds.PixelData = np.zeros((rows, cols), dtype=np.uint16).tobytes()
    ds.save_as(path, enforce_file_format=True)
    return sop_instance_uid


def _make_rtstruct(path, referenced_sop_instance_uids):
    """
    Write an RTSTRUCT whose ReferencedFrameOfReferenceSequence points at the
    given SOP Instance UIDs.
    """
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

    # --- ROIContourSequence ---
    roi_contour = Dataset()
    roi_contour.ROINumber = 1
    roi_contour.ROIDisplayColor = [255, 0, 0]
    roi_contour.ReferencedROINumber = 1
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

    contour_image_sequence = Dataset()
    contour_image_sequence.ContourImageSequence = contour_image_items

    referenced_series = Dataset()
    referenced_series.SeriesInstanceUID = "1.2.826.0.1.3680043.9.7.2"
    referenced_series.ContourImageSequence = contour_image_sequence.ContourImageSequence

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
def ct_series(tmp_path):
    """A directory of three CT slices; returns (dcm_dir, [sop_instance_uids])."""
    dcm_dir = tmp_path / "DCM"
    dcm_dir.mkdir()
    uids = []
    for i in range(3):
        uid = f"1.2.826.0.1.3680043.9.7.10.{i}"
        _make_ct_slice(str(dcm_dir / f"slice_{i}.dcm"), uid, i)
        uids.append(uid)
    return dcm_dir, uids


# ---------------------------------------------------------------- tests

def test_patch_injects_missing_warnings_module():
    """The shim must make rt_utils.rtstruct_builder.warnings resolvable."""
    from rt_utils import rtstruct_builder

    # Simulate the unpatched 1.2.7 state.
    if hasattr(rtstruct_builder, "warnings"):
        del rtstruct_builder.warnings

    assert patch_rt_utils_warnings() is True
    assert hasattr(rtstruct_builder, "warnings")


def test_package_import_applies_the_patch():
    """
    Importing netrt_core must leave rt_utils patched, so every rt_utils call
    site in the package (contour_processor and segmentation_processor) is
    protected without having to import the shim in each module.
    """
    import netrt_core  # noqa: F401  (import for side effect)
    from rt_utils import rtstruct_builder

    assert hasattr(rtstruct_builder, "warnings")


def test_bootstrap_never_raises():
    """The bootstrap must swallow errors so the app can always start."""
    assert patch_rt_utils_compat_bootstrap() is None


def test_patch_is_idempotent():
    """Applying the patch twice must not raise and must report it did nothing."""
    from rt_utils import rtstruct_builder

    if hasattr(rtstruct_builder, "warnings"):
        del rtstruct_builder.warnings

    assert patch_rt_utils_warnings() is True
    assert patch_rt_utils_warnings() is False


def test_unpatched_library_raises_nameerror():
    """
    Documents the upstream bug: without the shim, the warn_only path raises
    NameError instead of warning. This is the traceback reported in issue #1.
    """
    from rt_utils import rtstruct_builder
    from rt_utils.rtstruct_builder import RTStructBuilder

    saved = getattr(rtstruct_builder, "warnings", None)
    if saved is not None:
        del rtstruct_builder.warnings
    try:
        contour_image = Dataset()
        contour_image.ReferencedSOPInstanceUID = "1.2.3.4.5"
        series_data = [Dataset()]
        series_data[0].SOPInstanceUID = "9.9.9.9"

        with pytest.raises(NameError):
            RTStructBuilder.validate_contour_image_in_series_data(
                contour_image, series_data, True
            )
    finally:
        if saved is not None:
            rtstruct_builder.warnings = saved


def test_patched_library_warns_instead_of_raising():
    """With the shim, the warn_only path emits a warning and returns normally."""
    from rt_utils.rtstruct_builder import RTStructBuilder

    patch_rt_utils_warnings()

    contour_image = Dataset()
    contour_image.ReferencedSOPInstanceUID = "1.2.3.4.5"
    series_data = [Dataset()]
    series_data[0].SOPInstanceUID = "9.9.9.9"

    with pytest.warns(UserWarning, match="not contained in input series data"):
        RTStructBuilder.validate_contour_image_in_series_data(
            contour_image, series_data, True
        )


def test_create_from_warns_and_succeeds_on_dangling_reference(ct_series, tmp_path):
    """
    End-to-end: an RTSTRUCT referencing a CT slice that is NOT in the series
    must warn and let processing continue (the scenario from issue #1).
    """
    from rt_utils.rtstruct_builder import RTStructBuilder

    dcm_dir, uids = ct_series
    missing_uid = "1.2.826.0.1.3680043.9.7.99"  # referenced but not present
    struct_path = _make_rtstruct(
        str(tmp_path / "struct.dcm"), uids + [missing_uid]
    )

    patch_rt_utils_warnings()

    with pytest.warns(UserWarning, match="not contained in input series data"):
        rt_struct = RTStructBuilder.create_from(
            dicom_series_path=str(dcm_dir),
            rt_struct_path=struct_path,
            warn_only=True,
        )

    assert rt_struct is not None
    assert rt_struct.get_roi_names() == ["BODY"]


def test_create_from_still_raises_when_warn_only_is_false(ct_series, tmp_path):
    """
    The shim must not weaken strict validation: warn_only=False has to keep
    raising an Exception.
    """
    from rt_utils.rtstruct_builder import RTStructBuilder

    dcm_dir, uids = ct_series
    missing_uid = "1.2.826.0.1.3680043.9.7.99"
    struct_path = _make_rtstruct(
        str(tmp_path / "struct.dcm"), uids + [missing_uid]
    )

    patch_rt_utils_warnings()

    with pytest.raises(Exception, match="not contained in input series data"):
        RTStructBuilder.create_from(
            dicom_series_path=str(dcm_dir),
            rt_struct_path=struct_path,
            warn_only=False,
        )


def test_create_from_succeeds_when_all_references_resolve(ct_series, tmp_path):
    """Sanity check: a fully resolvable RTSTRUCT produces no warning."""
    from rt_utils.rtstruct_builder import RTStructBuilder

    dcm_dir, uids = ct_series
    struct_path = _make_rtstruct(str(tmp_path / "struct.dcm"), uids)

    patch_rt_utils_warnings()

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        rt_struct = RTStructBuilder.create_from(
            dicom_series_path=str(dcm_dir),
            rt_struct_path=struct_path,
            warn_only=True,
        )

    assert rt_struct is not None
    assert not [
        w for w in caught
        if "not contained in input series data" in str(w.message)
    ]
