# CORRECT - DICOM RT Structure Processor

CORRECT is a DICOM service that automatically processes RT Structure Sets to create contour overlay series. It listens for DICOM studies on a network port, extracts contour data from RTSTRUCT files, merges them into binary masks, and creates new DICOM series with graphical overlays.

## Key Features

- **DICOM Network Listener**: Receives DICOM studies via C-STORE operations
- **Automated RT Processing**: Extracts and merges contour data from RTSTRUCT files
- **Overlay Generation**: Creates new DICOM series with contour masks as overlay planes
- **Configurable Anonymization**: Independent site-code ID replacement and tag removal/blanking rules
- **Debug Visualization**: Optional JPG and DICOM debug output for quality assurance
- **Transaction Logging**: Detailed audit trails for all processing operations

## Quick Start with Docker

### Prerequisites

- Docker and Docker Compose
- Network access to source and destination DICOM systems

### Setup

1. **Clone and Configure**
   ```bash
   git clone <repository_url>
   cd NETRT
   ```

2. **Edit Configuration**
   
   Modify `config.yaml` to set your DICOM network parameters:
   ```yaml
   dicom_listener:
     host: "0.0.0.0"
     port: 11112
     ae_title: "CORRECT"
   
   dicom_destination:
     ip: "192.168.1.100"
     port: 104
     ae_title: "DESTINATION_AET"
   ```

3. **Create Data Directories**
   ```bash
   mkdir -p /DATA/netrt_data/{working,logs}
   ```

4. **Deploy**
   ```bash
   docker compose up --build -d
   ```

5. **Verify Operation**
   ```bash
   docker compose logs -f
   ```

### Management

- **View Logs**: `docker compose logs -f`
- **Stop Service**: `docker compose down`
- **Restart**: `docker compose restart`

## Configuration

All settings are managed through `config.yaml`. Key parameters include:

- **Network Settings**: DICOM listener and destination configuration
- **Processing Options**: Contour filtering rules, series descriptions
- **Anonymization**: Two independent controls — `apply_site_code` for patient ID replacement and `apply_anonymization_rules` for tag removal/blanking
- **Directories**: Working and log file locations

See [CONFIGURATION.md](CONFIGURATION.md) for complete configuration reference.

## Processing Workflow

1. **Reception**: DICOM files received via C-STORE and organized by StudyInstanceUID
2. **Detection**: File system monitoring triggers processing after transfer completion
3. **Processing**: RT Structure contours extracted, filtered, and merged into overlay masks
4. **Output**: New DICOM series created with contour overlays in overlay planes
5. **Transmission**: Processed series sent to configured destination
6. **Cleanup**: Temporary files removed after successful transmission

## Documentation

- [ARCHITECTURE.md](ARCHITECTURE.md): System design and component overview
- [CONFIGURATION.md](CONFIGURATION.md): Complete configuration reference
- [DEPLOYMENT.md](DEPLOYMENT.md): Docker and systemd deployment instructions
- [DETAILS.md](DETAILS.md): Technical implementation details

## Requirements

- Python 3.8+
- DICOM network connectivity
- Sufficient disk space for temporary study storage

## Production Considerations

- Configure appropriate disk space monitoring for working directories
- Set up log rotation for application and transaction logs
- Implement network security controls for DICOM communications
- Review anonymization settings (`apply_site_code`, `apply_anonymization_rules`) to meet your privacy requirements
- Monitor quarantine directory for failed studies

## Support

For configuration issues, check the application logs in the configured logs directory. Failed studies are automatically moved to the quarantine subdirectory for manual review.