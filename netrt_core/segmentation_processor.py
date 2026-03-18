import os
import logging
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
        rt_struct = RTStructBuilder.create_from(
            dicom_series_path=ct_dicom_dir,
            rt_struct_path=rtstruct_path,
        )

        ignore_terms = self.processing_config.get("ignore_contour_names_containing", ["skull"])
        all_rois = rt_struct.get_roi_names()
        rois = [r for r in all_rois if not any(t.lower() in r.lower() for t in ignore_terms)]

        logger.info(f"Creating DICOM SEG for {len(rois)} ROIs (filtered from {len(all_rois)} total)")

        ct_datasets = self._load_sorted_ct_datasets(ct_dicom_dir)
        if not ct_datasets:
            raise ValueError("No CT datasets found to use as SEG source images.")

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

    def _load_sorted_ct_datasets(self, ct_dicom_dir: str) -> list:
        """Loads CT DICOM datasets sorted ascending by SliceLocation."""
        files = [f for f in os.listdir(ct_dicom_dir) if f.lower().endswith(".dcm")]
        datasets_with_location = []
        for filename in files:
            try:
                ds = dcmread(os.path.join(ct_dicom_dir, filename))
                if hasattr(ds, "SliceLocation"):
                    datasets_with_location.append((float(ds.SliceLocation), ds))
            except Exception as e:
                logger.warning(f"Could not read CT file {filename}: {e}")

        datasets_with_location.sort(key=lambda x: x[0])
        return [ds for _, ds in datasets_with_location]
