import os
import collections
import logging
import shutil
import tempfile
import numpy as np
from pydicom import dcmread
from rt_utils import RTStructBuilder
import highdicom as hd
from highdicom.sr.coding import CodedConcept

logger = logging.getLogger(__name__)


class SegmentationProcessor:
    """Creates DICOM SEG files from RTSTRUCT contour data — one file per ROI."""

    def __init__(self, config):
        self.config = config
        self.processing_config = config.get("processing", {})

    def _get_compatible_ct_dir(self, ct_dicom_dir: str):
        """
        Returns (path_for_rt_utils, temp_dir_or_None) where the path contains only
        DICOM images from a single CT series with consistent dimensions and the spatial
        tags that rt-utils requires (ImagePositionPatient).

        The DCM directory can contain mixed-series images (e.g. CT at 512×512 from one
        series, DRR/localization from another series at a smaller size).  This causes
        two failure modes:
          - rt-utils silently skips images it can't use, producing a mask with fewer
            slices than _load_sorted_ct_datasets loads → slice-count mismatch.
          - highdicom rejects source_images that span multiple SeriesInstanceUIDs or
            have inconsistent Rows/Columns.

        We filter to the majority (SeriesInstanceUID, Rows, Columns) group, which is
        always the main CT series.  Both rt-utils and highdicom then see a consistent,
        single-series image set.

        The caller must delete temp_dir when it is not None.
        """
        dcm_files = [f for f in os.listdir(ct_dicom_dir) if f.lower().endswith(".dcm")]
        if not dcm_files:
            return ct_dicom_dir, None

        group_counter = collections.Counter()
        file_info = {}  # filename -> (series_uid, rows, cols, has_position)
        for f in dcm_files:
            try:
                ds = dcmread(os.path.join(ct_dicom_dir, f), stop_before_pixels=True)
                if hasattr(ds, "Rows") and hasattr(ds, "Columns"):
                    series_uid = getattr(ds, "SeriesInstanceUID", "")
                    dims = (str(series_uid), int(ds.Rows), int(ds.Columns))
                    has_pos = hasattr(ds, "ImagePositionPatient")
                    file_info[f] = (dims, has_pos)
                    group_counter[dims] += 1
            except Exception:
                pass

        if not group_counter:
            return ct_dicom_dir, None

        majority_group, majority_count = group_counter.most_common(1)[0]
        total = sum(group_counter.values())
        valid_files = [f for f, (dims, has_pos) in file_info.items()
                       if dims == majority_group and has_pos]

        # If all files already pass both filters, no temp dir needed
        if len(valid_files) == total:
            return ct_dicom_dir, None

        logger.warning(
            f"DCM directory has {total} images across {len(group_counter)} series/dimension "
            f"groups. Keeping {len(valid_files)} images from the majority group "
            f"(series {majority_group[0][:16]}…, {majority_group[1]}×{majority_group[2]}) "
            f"for rt-utils and highdicom compatibility."
        )

        temp_dir = tempfile.mkdtemp(prefix="correct_seg_filtered_")
        for f in valid_files:
            os.symlink(os.path.join(ct_dicom_dir, f), os.path.join(temp_dir, f))

        return temp_dir, temp_dir

    def create_segmentations(self, rtstruct_path: str, ct_dicom_dir: str, output_dir: str) -> list:
        """
        Creates one DICOM SEG file per ROI in the RTSTRUCT.

        Args:
            rtstruct_path: Path to the RTSTRUCT .dcm file.
            ct_dicom_dir: Path to the directory containing the CT DICOM series.
            output_dir: Directory where SEG files will be saved.

        Returns:
            list[str]: Paths to created SEG files.
        """
        filtered_ct_dir, temp_dir = self._get_compatible_ct_dir(ct_dicom_dir)
        try:
            rt_struct = RTStructBuilder.create_from(
                dicom_series_path=filtered_ct_dir,
                rt_struct_path=rtstruct_path,
            )

            ignore_terms = self.processing_config.get("ignore_contour_names_containing", ["skull"])
            all_rois = rt_struct.get_roi_names()
            rois = [r for r in all_rois if not any(t.lower() in r.lower() for t in ignore_terms)]

            logger.info(f"Creating DICOM SEG for {len(rois)} ROIs (filtered from {len(all_rois)} total)")

            ct_datasets = self._load_sorted_ct_datasets(filtered_ct_dir)
            if not ct_datasets:
                raise ValueError("No CT datasets found to use as SEG source images.")

            # highdicom copies several study/patient tags from source images into
            # the SEG header. Ensure they exist even if stripped by anonymization.
            for ds in ct_datasets:
                for tag in ("PatientBirthDate", "PatientSex", "AccessionNumber", "StudyID"):
                    if not hasattr(ds, tag):
                        setattr(ds, tag, "")

            logger.info(f"Loaded {len(ct_datasets)} CT datasets for SEG source images.")

            os.makedirs(output_dir, exist_ok=True)

            series_base_number = self.processing_config.get("segmentation_series_number", 99)
            series_desc_template = self.processing_config.get(
                "segmentation_series_description_template", "RESEARCH USE ONLY: CONTOUR {}"
            )
            algo_name = self.processing_config.get("segmentation_algorithm_name", "Radiation Oncologist")
            algo_version = self.processing_config.get("segmentation_algorithm_version", "v1.0")
            tracking_id = self.processing_config.get("segmentation_tracking_id", "FOR RESEARCH USE ONLY")

            algo_id = hd.AlgorithmIdentificationSequence(
                name=algo_name,
                family=CodedConcept(
                    value="123109006",
                    scheme_designator="SCT",
                    meaning="Manual Segmentation",
                ),
                version=algo_version,
            )

            prop_category = CodedConcept(
                value="49755003",
                scheme_designator="SCT",
                meaning="Morphologically Altered Structure",
            )
            prop_type = CodedConcept(
                value="228793007",
                scheme_designator="SCT",
                meaning="Structure",
            )

            created_files = []
            for idx, roi_name in enumerate(rois):
                try:
                    # rt-utils returns (H, W, N_slices); highdicom expects (N_slices, H, W)
                    mask_hwn = rt_struct.get_roi_mask_by_name(roi_name)
                    mask_nhw = np.transpose(mask_hwn, (2, 0, 1)).astype(np.uint8)

                    if mask_nhw.shape[0] != len(ct_datasets):
                        raise ValueError(
                            f"ROI '{roi_name}': mask has {mask_nhw.shape[0]} slices but "
                            f"{len(ct_datasets)} CT datasets were loaded — slice count mismatch."
                        )

                    seg_desc = hd.seg.SegmentDescription(
                        segment_number=1,
                        segment_label=roi_name,
                        segmented_property_category=prop_category,
                        segmented_property_type=prop_type,
                        algorithm_type=hd.seg.SegmentAlgorithmTypeValues.SEMIAUTOMATIC,
                        algorithm_identification=algo_id,
                        tracking_uid=hd.UID(),
                        tracking_id=tracking_id,
                    )

                    seg = hd.seg.Segmentation(
                        source_images=ct_datasets,
                        pixel_array=mask_nhw,
                        segmentation_type=hd.seg.SegmentationTypeValues.BINARY,
                        segment_descriptions=[seg_desc],
                        series_instance_uid=hd.UID(),
                        series_number=series_base_number + idx,
                        sop_instance_uid=hd.UID(),
                        instance_number=idx + 1,
                        manufacturer="CORRECT",
                        manufacturer_model_name="CORRECT",
                        software_versions="1.0",
                        device_serial_number="1",
                        series_description=series_desc_template.format(roi_name),
                    )

                    safe_name = roi_name.replace(" ", "_").replace("/", "_").replace("\\", "_")
                    output_path = os.path.join(output_dir, f"SEG_{safe_name}.dcm")
                    seg.save_as(output_path, enforce_file_format=True)
                    created_files.append(output_path)
                    logger.info(f"Created SEG for ROI '{roi_name}': {output_path}")

                except Exception as e:
                    logger.error(f"Failed to create SEG for ROI '{roi_name}': {e}", exc_info=True)
                    continue

            return created_files
        finally:
            if temp_dir and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)

    def _load_sorted_ct_datasets(self, ct_dicom_dir: str) -> list:
        """Loads all CT DICOM datasets sorted by z-position, matching rt-utils slice ordering.

        Sort priority: ImagePositionPatient[2] → SliceLocation → InstanceNumber → filename.
        All files are loaded regardless of which tags are present so that the resulting
        list length matches the mask array produced by rt-utils.
        """
        files = [f for f in os.listdir(ct_dicom_dir) if f.lower().endswith(".dcm")]
        datasets = []
        for filename in files:
            try:
                ds = dcmread(os.path.join(ct_dicom_dir, filename))
                datasets.append((filename, ds))
            except Exception as e:
                logger.warning(f"Could not read CT file {filename}: {e}")

        def sort_key(item):
            filename, ds = item
            if hasattr(ds, "ImagePositionPatient"):
                return (0, float(ds.ImagePositionPatient[2]))
            if hasattr(ds, "SliceLocation"):
                return (1, float(ds.SliceLocation))
            if hasattr(ds, "InstanceNumber"):
                return (2, float(ds.InstanceNumber))
            return (3, 0.0)

        datasets.sort(key=sort_key)
        return [ds for _, ds in datasets]
