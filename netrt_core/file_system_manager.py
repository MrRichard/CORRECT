import os
import shutil
import logging
import time
import pydicom
import threading
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

logger = logging.getLogger(__name__)

class NewStudyEventHandler(FileSystemEventHandler):
    """Handles file system events to detect newly completed studies.
    
    This class is responsible for monitoring the file system for new DICOM studies,
    implementing a debounce mechanism to ensure studies are only processed once
    they have completely arrived, and triggering processing when appropriate.
    
    Attributes:
        fsm: Reference to FileSystemManager for accessing configuration and paths
        study_processor_callback: Function to call when a study is ready for processing
        study_activity_timers: Dictionary tracking timers for each study's debounce period
        debounce_interval: Time in seconds to wait after last file activity before processing
        min_file_count_for_processing: Minimum number of files required to process a study
    """
    def __init__(self, file_system_manager, study_processor_callback):
        super().__init__()
        self.fsm = file_system_manager
        self.study_processor_callback = study_processor_callback
        # Use a dictionary to track activity in study directories and debounce
        self.study_activity_timers = {}
        self.debounce_interval = self.fsm.config.get("watcher", {}).get("debounce_interval_seconds", 60 )
        self.min_file_count_for_processing = self.fsm.config.get("watcher", {}).get("min_file_count_for_processing", 5 )

    # ======== Path and Study UID Helper Methods ========
    
    def _is_processing_output_dir(self, path):
        """Check if path is in a processing output directory that should be ignored for triggering.
        
        Processing output directories contain result files that shouldn't trigger additional
        processing to avoid infinite processing loops.
        
        Args:
            path: File system path to check
            
        Returns:
            bool: True if path contains output directory names like "Addition" or "Segmentations"
        """
        path_parts = path.split(os.sep)
        # Check if any part of the path contains these directory names
        return any(part in ["Addition", "Segmentations", "SC"] for part in path_parts)

    def _is_study_directory(self, path):
        """Determines if a path is a valid study directory.

        Study directories are named with the pattern "UID_<StudyInstanceUID>" and must
        be direct children of working_dir. This excludes quarantined studies and any
        other nested UID_ directories to prevent re-processing loops.

        Args:
            path: File system path to check

        Returns:
            bool: True if path is a direct child of working_dir with "UID_" prefix
        """
        return (
            os.path.isdir(path)
            and os.path.basename(path).startswith("UID_")
            and os.path.dirname(path) == self.fsm.working_dir
        )

    def _get_study_uid_from_path(self, path):
        """Extracts the StudyInstanceUID from a study directory path.
        
        Args:
            path: Study directory path to parse
            
        Returns:
            str: StudyInstanceUID extracted from path, or None if not a valid study directory
        """
        basename = os.path.basename(path)
        if basename.startswith("UID_"):
            return basename[4:] # Remove "UID_" prefix
        return None

    # ======== Study Processing Control Methods ========
    
    def _check_and_process_study(self, study_path):
        """Checks if a study is ready for processing and initiates processing if it is.
        
        This method performs several checks to determine if a study directory contains
        sufficient data for processing:
        1. Validates the study UID
        2. Checks for presence of required DCM directory
        3. Counts files to ensure minimum threshold is met
        4. Acquires a processing lock to prevent concurrent processing
        5. Calls the study processor callback if all conditions are met
        
        Args:
            study_path: Path to the study directory to check
        """
        study_uid = self._get_study_uid_from_path(study_path)
        if not study_uid:
            return

        # Basic check: does DCM directory exist and contain files?
        dcm_dir = os.path.join(study_path, "DCM")
        if not os.path.isdir(dcm_dir) or not os.listdir(dcm_dir):
            logger.debug(f"Debounce check: DCM directory for {study_uid} is still empty or not found. Waiting.")
            return
        
        # Count total files in DCM and Structure directories to ensure sufficient content
        total_files = 0
        if os.path.isdir(dcm_dir):
            total_files += len([f for f in os.listdir(dcm_dir) if os.path.isfile(os.path.join(dcm_dir, f))])
        struct_dir = os.path.join(study_path, "Structure")
        if os.path.isdir(struct_dir):
            total_files += len([f for f in os.listdir(struct_dir) if os.path.isfile(os.path.join(struct_dir, f))])

        # Skip processing if minimum file threshold not met
        if total_files < self.min_file_count_for_processing:
            logger.debug(f"Debounce check: Study {study_uid} has only {total_files} files, less than min {self.min_file_count_for_processing}. Waiting.")
            return

        # Prevent duplicate processing attempts
        if self.fsm.is_study_being_processed(study_uid):
            logger.info(f"Study {study_uid} is already being processed. Skipping duplicate processing.")
            return

        # Try to acquire processing lock (non-blocking)
        if not self.fsm.acquire_study_lock(study_uid):
            logger.info(f"Could not acquire lock for study {study_uid}. It may be in processing by another thread.")
            return

        logger.info(f"Debounce interval ended for study {study_uid}. Triggering processing.")
        if study_uid in self.study_activity_timers:
            # Remove timer from tracking dictionary
            del self.study_activity_timers[study_uid]
            
            try:
                # Call the study processor callback to begin processing
                self.study_processor_callback(study_uid)
            except Exception as e:
                logger.error(f"Error during study processing for {study_uid}: {e}", exc_info=True)
            finally:
                # Always release the lock when done, even if processing fails
                self.fsm.release_study_lock(study_uid)

    def _start_debounce_timer(self, study_path):
        """Starts or resets the debounce timer for a study.
        
        When files are being actively written to a study directory, this method
        ensures a cooldown period after the last file activity before processing
        is triggered. If a new file arrives during the cooldown, the timer is
        reset to prevent premature processing.
        
        Args:
            study_path: Path to the study directory to monitor
        """
        study_uid = self._get_study_uid_from_path(study_path)
        if not study_uid:
            return

        # Cancel any existing timer for this study
        if study_uid in self.study_activity_timers:
            self.study_activity_timers[study_uid].cancel()
        
        # Create and start a new timer that will call _check_and_process_study
        # after the debounce interval elapses
        timer = threading.Timer(self.debounce_interval, self._check_and_process_study, args=[study_path])
        self.study_activity_timers[study_uid] = timer
        timer.start()
        logger.debug(f"Started debounce timer for study {study_uid} in path {study_path}")

    # ======== File System Event Handlers ========
    
    def on_created(self, event):
        """Handles file or directory creation events.
        
        This method is called when a new file or directory is created
        in the monitored working directory. For study directories or files
        within study directories, it starts a debounce timer.
        
        Args:
            event: Watchdog FileSystemEvent object containing path and event information
        """
        super().on_created(event)
        
        # Case 1: A new study directory was created
        if event.is_directory and self._is_study_directory(event.src_path):
            logger.info(f"New study directory created: {event.src_path}")
            self._start_debounce_timer(event.src_path)
        
        # Case 2: A new file was created within a study directory
        elif not event.is_directory:
            # Skip files in processing output directories
            if self._is_processing_output_dir(event.src_path):
                logger.debug(f"Ignoring file creation event in processing directory: {event.src_path}")
                return
                
            # Check if the file is within a study directory structure
            # Assumes directory structure: <working_dir>/UID_<study_uid>/<subdir>/<filename>
            study_dir = os.path.dirname(os.path.dirname(event.src_path))
            if self._is_study_directory(study_dir):
                logger.debug(f"File created in study {study_dir}: {event.src_path}")
                self._start_debounce_timer(study_dir)

    def on_modified(self, event):
        """Handles file or directory modification events.
        
        This method is called when a file or directory is modified
        in the monitored working directory. For study directories or files
        within study directories, it starts or resets the debounce timer.
        
        Args:
            event: Watchdog FileSystemEvent object containing path and event information
        """
        super().on_modified(event)
        
        # Case 1: A study directory was modified
        if event.is_directory and self._is_study_directory(event.src_path):
            logger.debug(f"Study directory modified: {event.src_path}")
            self._start_debounce_timer(event.src_path)
        
        # Case 2: A file within a study directory was modified
        elif not event.is_directory:
            # Skip files in processing output directories
            if self._is_processing_output_dir(event.src_path):
                logger.debug(f"Ignoring file modification event in processing directory: {event.src_path}")
                return
                
            # Check if the file is within a study directory structure
            study_dir = os.path.dirname(os.path.dirname(event.src_path))
            if self._is_study_directory(study_dir):
                logger.debug(f"File modified in study {study_dir}: {event.src_path}")
                self._start_debounce_timer(study_dir)
        
    def on_closed(self, event):
        """Handles file close events.
        
        This method is called when a file that was being written is closed.
        This is often the most reliable indicator that a file transfer is complete,
        especially for DICOM files transmitted over the network.
        
        Args:
            event: Watchdog FileSystemEvent object containing path and event information
        """
        super().on_closed(event)
        
        # Only handle file (not directory) close events
        if not event.is_directory:
            # Skip files in processing output directories
            if self._is_processing_output_dir(event.src_path):
                logger.debug(f"Ignoring file closed event in processing directory: {event.src_path}")
                return
                
            # Check if the file is within a study directory structure
            study_dir = os.path.dirname(os.path.dirname(event.src_path))
            if self._is_study_directory(study_dir):
                logger.info(f"File closed in study {study_dir}: {event.src_path}. Debouncing.")
                self._start_debounce_timer(study_dir)

class FileSystemManager:
    """Manages DICOM file operations and study directory structure in the working directory.
    
    This class is responsible for creating and maintaining the directory structure,
    saving incoming DICOM files, coordinating study processing through callbacks,
    and handling study lifecycle events (creation, quarantine, cleanup).
    
    The FileSystemManager also handles concurrent processing locks to prevent
    multiple processing attempts on the same study.
    
    Attributes:
        config: Application configuration dictionary
        working_dir: Base directory where all studies are stored
        quarantine_dir: Directory for studies with processing errors
        logs_dir: Directory for log files
        processing_locks: Dictionary of thread locks for each study being processed
        observer: Watchdog observer for file system monitoring
        event_handler: Handler for file system events that trigger study processing
    """
    
    def __init__(self, config, study_processor_callback=None):
        """Initialize the FileSystemManager.

        Args:
            config (dict): Application configuration containing directory paths.
            study_processor_callback (callable): Callback for when a new study is ready.
        """
        self.config = config
        self.working_dir = os.path.expanduser(config.get("directories", {}).get("working", "~/CORRECT_working"))
        self.quarantine_subdir = config.get("directories", {}).get("quarantine_subdir", "quarantine")
        self.quarantine_dir = os.path.join(self.working_dir, self.quarantine_subdir)
        self.logs_dir = os.path.expanduser(config.get("directories", {}).get("logs", "~/CORRECT_logs"))

        # Add a lock dictionary to track processing studies
        self.processing_locks = {}

        os.makedirs(self.working_dir, exist_ok=True)
        os.makedirs(self.quarantine_dir, exist_ok=True)
        os.makedirs(self.logs_dir, exist_ok=True)
        logger.info(f"FileSystemManager initialized. Working: {self.working_dir}, Quarantine: {self.quarantine_dir}, Logs: {self.logs_dir}")
        
        self.study_processor_callback = study_processor_callback
        self.observer = None
        if self.study_processor_callback:
            self.event_handler = NewStudyEventHandler(self, self.study_processor_callback)
            self.observer = Observer()
        else:
            logger.warning("No study_processor_callback provided to FileSystemManager. Watchdog observer not started.")

    # ======== Study Processing Lock Management ========

    def is_study_being_processed(self, study_uid):
        """Check if a study is currently being processed.
        
        This method checks if a lock exists and is held for the given study.
        Used to prevent concurrent processing of the same study.
        
        Args:
            study_uid: StudyInstanceUID string
            
        Returns:
            bool: True if the study is currently locked for processing
        """
        return study_uid in self.processing_locks and self.processing_locks[study_uid].locked()

    def acquire_study_lock(self, study_uid):
        """Try to acquire a lock for study processing.
        
        Creates a lock if one doesn't exist, and attempts to acquire it
        in a non-blocking manner to prevent deadlocks.
        
        Args:
            study_uid: StudyInstanceUID string
            
        Returns:
            bool: True if lock was successfully acquired, False if already locked
        """
        if study_uid not in self.processing_locks:
            self.processing_locks[study_uid] = threading.Lock()
        
        # Try to acquire the lock without blocking to prevent deadlocks
        return self.processing_locks[study_uid].acquire(blocking=False)

    def release_study_lock(self, study_uid):
        """Release the processing lock for a study.
        
        This should always be called after processing is complete or if an
        error occurs during processing, typically in a finally block.
        
        Args:
            study_uid: StudyInstanceUID string
        """
        if study_uid in self.processing_locks and self.processing_locks[study_uid].locked():
            self.processing_locks[study_uid].release()
            logger.debug(f"Released processing lock for study {study_uid}")

    # ======== Directory Watching and Monitoring ========
    
    def start_watching(self):
        """Start the file system observer to monitor for new studies."""
        if self.observer and self.study_processor_callback:
            # Use polling observer for better cross-platform compatibility
            from watchdog.observers.polling import PollingObserver
            
            # Detect if we should use polling (helpful for Docker on Windows)
            use_polling = os.environ.get('WATCHDOG_USE_POLLING', 'false').lower() == 'true'
            
            if use_polling:
                logger.info("Using PollingObserver for file system monitoring (better for Windows/Docker)")
                self.observer.stop()  # Stop the regular observer
                self.observer = PollingObserver()
            
            self.observer.schedule(self.event_handler, self.working_dir, recursive=True)
            try:
                self.observer.start()
                logger.info(f"Started watching working directory for new studies: {self.working_dir}")
            except Exception as e:
                logger.error(f"Failed to start watchdog observer: {e}", exc_info=True)
        elif not self.study_processor_callback:
            logger.error("Cannot start watching: study_processor_callback was not provided during FSM initialization.")
        else:
            logger.info("Watchdog observer not configured, not starting.")

    def stop_watching(self):
        """Stop the file system observer.
        
        This method should be called during system shutdown to gracefully
        stop the watchdog observer and cancel any pending debounce timers.
        """
        if self.observer and self.observer.is_alive():
            # Cancel all active debounce timers before stopping the observer
            if hasattr(self.event_handler, "study_activity_timers"):
                for study_uid, timer in list(self.event_handler.study_activity_timers.items()):
                    timer.cancel()
                    logger.debug(f"Cancelled active debounce timer for study {study_uid} during shutdown.")
                self.event_handler.study_activity_timers.clear()
            
            # Gracefully stop and wait for the observer thread to exit
            self.observer.stop()
            self.observer.join()
            logger.info("Stopped watching working directory.")

    # ======== Study Path and File Management ========
    
    def get_study_path(self, study_instance_uid):
        """Get the full path to a study directory based on its UID.
        
        Args:
            study_instance_uid: DICOM StudyInstanceUID string
            
        Returns:
            str: Absolute path to the study directory
        """
        return os.path.join(self.working_dir, f"UID_{study_instance_uid}")

    def save_incoming_dicom(self, dataset, file_meta, negotiated_transfer_syntax=None):
        """Save an incoming DICOM dataset to the appropriate directory structure.
        
        This method handles:
        1. Creating the appropriate directory structure for the study
        2. Determining the directory (DCM or Structure) based on SOPClassUID
        3. Setting up the proper DICOM file meta information
        4. Ensuring proper transfer syntax and DICOM file format
        5. Saving the file to disk with the correct filename
        
        Args:
            dataset: pydicom Dataset containing the DICOM instance
            file_meta: DICOM file meta information
            negotiated_transfer_syntax: Optional transfer syntax from network negotiation
            
        Returns:
            str: Path to the saved file, or None if an error occurred
        """
        try:
            # Extract key identifiers from the dataset
            study_uid = dataset.StudyInstanceUID
            sop_instance_uid = dataset.SOPInstanceUID
            study_path = self.get_study_path(study_uid)

            # Determine if this is an RTSTRUCT file based on SOPClassUID or Modality
            sop_class_uid = dataset.SOPClassUID
            modality = getattr(dataset, "Modality", "").upper()
            is_rtstruct = "1.2.840.10008.5.1.4.1.1.481.3" in str(sop_class_uid) or modality == "RTSTRUCT"

            # Put RTSTRUCTs in the Structure directory, all other files in DCM
            series_dir_name = "Structure" if is_rtstruct else "DCM"
            series_path = os.path.join(study_path, series_dir_name)
            os.makedirs(series_path, exist_ok=True)
            
            # Create filename based on SOPInstanceUID
            filename = f"{sop_instance_uid}.dcm"
            filepath = os.path.join(series_path, filename)
            
            # Ensure file_meta contains required DICOM meta information
            ds_file_meta = pydicom.Dataset(file_meta)  # Make a copy to modify
            ds_file_meta.MediaStorageSOPClassUID = sop_class_uid
            ds_file_meta.MediaStorageSOPInstanceUID = sop_instance_uid
            
            # Set proper transfer syntax
            if negotiated_transfer_syntax:
                ds_file_meta.TransferSyntaxUID = negotiated_transfer_syntax
            elif not hasattr(ds_file_meta, "TransferSyntaxUID") or not ds_file_meta.TransferSyntaxUID:
                logger.warning(f"No negotiated transfer syntax provided for {sop_instance_uid}. Defaulting to ImplicitVRLittleEndian.")
                ds_file_meta.TransferSyntaxUID = pydicom.uid.ImplicitVRLittleEndian

            # Set implementation identifiers
            ds_file_meta.ImplementationClassUID = pydicom.uid.PYDICOM_IMPLEMENTATION_UID
            ds_file_meta.ImplementationVersionName = "NETRT_CORE_WD_0.1"

            # Attach the file meta to the dataset
            dataset.file_meta = ds_file_meta
            
            # Set appropriate VR and endianness based on transfer syntax
            if not hasattr(dataset, 'is_implicit_VR') or not hasattr(dataset, 'is_little_endian'):
                if ds_file_meta.TransferSyntaxUID.is_implicit_VR:
                    dataset.is_implicit_VR = True
                    dataset.is_little_endian = True  # Implicit VR is always Little Endian
                else:  # Explicit VR
                    dataset.is_implicit_VR = False
                    dataset.is_little_endian = ds_file_meta.TransferSyntaxUID.is_little_endian

            # Save the dataset with proper DICOM format
            dataset.save_as(filepath, enforce_file_format=True)
            logger.info(f"Saved DICOM file: {filepath} with TS: {ds_file_meta.TransferSyntaxUID}")
            return filepath
        except Exception as e:
            logger.error(f"Error saving DICOM file {getattr(dataset, 'SOPInstanceUID', 'UNKNOWN_SOPUID')}: {e}", exc_info=True)
            return None

    # ======== Study Lifecycle Management ========
    
    def quarantine_study(self, study_instance_uid, reason="Unknown error"):
        """Move a study to the quarantine directory due to processing errors.
        
        This method preserves problematic studies for later analysis by moving
        them to a quarantine area instead of deleting them.
        
        Args:
            study_instance_uid: StudyInstanceUID of the study to quarantine
            reason: Optional string explaining why the study is being quarantined
        """
        study_path = self.get_study_path(study_instance_uid)
        if os.path.exists(study_path):
            quarantine_study_path = os.path.join(self.quarantine_dir, f"UID_{study_instance_uid}")
            
            # Avoid overwriting existing quarantined studies by adding a timestamp
            if os.path.exists(quarantine_study_path):
                logger.warning(f"Quarantine path {quarantine_study_path} already exists. Appending timestamp.")
                quarantine_study_path = f"{quarantine_study_path}_{int(time.time())}"
                
            try:
                # Move the entire study directory to quarantine
                shutil.move(study_path, quarantine_study_path)
                logger.warning(f"Moved study {study_instance_uid} to quarantine: {quarantine_study_path}. Reason: {reason}")
            except Exception as e:
                logger.error(f"Failed to move study {study_instance_uid} to quarantine: {e}", exc_info=True)
        else:
            logger.warning(f"Attempted to quarantine non-existent study path: {study_path}")

    def cleanup_study_directory(self, study_instance_uid):
        """Remove a study directory after successful processing.
        
        This method deletes the study directory and all its contents once
        processing is complete and data has been sent to the destination.
        
        Args:
            study_instance_uid: StudyInstanceUID of the study to clean up
        """
        study_path = self.get_study_path(study_instance_uid)
        if os.path.exists(study_path):
            try:
                # Remove the entire study directory tree
                shutil.rmtree(study_path)
                logger.info(f"Successfully cleaned up study directory: {study_path}")
            except Exception as e:
                logger.error(f"Error cleaning up study directory {study_path}: {e}", exc_info=True)
        else:
            logger.info(f"Attempted to clean up non-existent study directory: {study_path}")