import os
import logging
import pydicom
import numpy as np
import cv2
import copy
import tempfile
import shutil

logger = logging.getLogger(__name__)

class BurnInProcessor:
    """A class to apply a text burn-in to a directory of DICOM images."""

    def __init__(self, burn_in_text):
        """
        Initializes the BurnInProcessor.

        Args:
            burn_in_text (str): The text to burn into the images.
        """
        self.burn_in_text = burn_in_text

    def run(self, directory_path):
        """
        Applies the burn-in to all DICOM files in the specified directory.

        Args:
            directory_path (str): The absolute path to the directory of DICOM files.
        """
        logger.info(f"Applying burn-in text to DICOM files in {directory_path}")
        for root, _, files in os.walk(directory_path):
            for file in files:
                if file.lower().endswith(".dcm"):
                    filepath = os.path.join(root, file)
                    self._apply_watermark_safe(filepath)

    def _apply_watermark_safe(self, filepath):
        """Safely applies a watermark to a single DICOM file."""
        try:
            dcm = pydicom.dcmread(filepath, force=True)
            if "PixelData" not in dcm:
                logger.warning(f"File {filepath} has no PixelData to apply burn-in. Skipping.")
                return

            pixel_array = dcm.pixel_array
            if pixel_array.ndim != 2:
                logger.debug(f"File {filepath} has non-2D pixel data (shape {pixel_array.shape}). Skipping burn-in.")
                return

            img_rescaled = self._rescale_pixel_array(pixel_array)
            img_watermarked = self._draw_text(img_rescaled)
            
            new_dcm = self._create_new_dicom_dataset(dcm, img_watermarked)

            # Write to a temporary file and then replace the original
            temp_fd, temp_path = tempfile.mkstemp(dir=os.path.dirname(filepath), prefix=".tmp-")
            pydicom.dcmwrite(temp_path, new_dcm, enforce_file_format=True)
            os.close(temp_fd)
            shutil.move(temp_path, filepath)
            logger.debug(f"Successfully applied burn-in to {filepath}")

        except Exception as e:
            logger.error(f"Could not apply burn-in to file {filepath}: {e}", exc_info=True)
            if 'temp_path' in locals() and os.path.exists(temp_path):
                os.remove(temp_path)
            raise

    def _rescale_pixel_array(self, pixel_array):
        """Rescales pixel data to 8-bit for image processing."""
        min_val = np.min(pixel_array)
        max_val = np.max(pixel_array)
        if max_val == min_val:
            return np.zeros(pixel_array.shape, dtype=np.uint8)
        return ((pixel_array - min_val) / (max_val - min_val) * 255).astype(np.uint8)

    def _draw_text(self, image):
        """Draws the configured burn-in text onto the image."""
        font = cv2.FONT_HERSHEY_SIMPLEX
        font_scale = 0.5
        color = (255, 255, 255) # White
        thickness = 1

        text_size = cv2.getTextSize(self.burn_in_text, font, font_scale, thickness)[0]
        
        # Position in the bottom-right corner
        x = image.shape[1] - text_size[0] - 10
        y = image.shape[0] - text_size[1] - 10

        # Draw a black rectangle behind the text for readability
        cv2.rectangle(image, (x, y + 2), (x + text_size[0], y - text_size[1] - 2), (0, 0, 0), cv2.FILLED)
        cv2.putText(image, self.burn_in_text, (x, y), font, font_scale, color, thickness, cv2.LINE_AA)
        return image

    def _create_new_dicom_dataset(self, original_dcm, watermarked_image_8bit):
        """Creates a new DICOM dataset with the watermarked pixel data."""
        new_dcm = copy.deepcopy(original_dcm)
        
        # Rescale the 8-bit watermarked image back to the original data range
        original_min = np.min(original_dcm.pixel_array)
        original_max = np.max(original_dcm.pixel_array)
        if original_max == original_min:
            rescaled_pixel_data = np.full(watermarked_image_8bit.shape, original_min, dtype=original_dcm.pixel_array.dtype)
        else:
            rescaled_pixel_data = ((watermarked_image_8bit / 255.0) * (original_max - original_min) + original_min)
        
        new_dcm.PixelData = rescaled_pixel_data.astype(original_dcm.pixel_array.dtype).tobytes()
        return new_dcm
