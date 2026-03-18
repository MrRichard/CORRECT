// OHIF Viewer configuration for use with the CORRECT PACS stack.
// Orthanc DICOMweb endpoints are accessed via the host on port 8042.
//
// Credentials must match pacs-config/orthanc.json RegisteredUsers.
// For production use, replace with a reverse proxy that handles auth.

window.config = {
  routerBasename: '/',
  showStudyList: true,
  filterQueryParam: false,
  defaultDataSourceName: 'dicomweb',

  dataSources: [
    {
      namespace: '@ohif/extension-default.dataSourcesModule.dicomweb',
      sourceName: 'dicomweb',
      configuration: {
        name: 'Orthanc',
        wadoUriRoot: 'http://localhost:8042/wado',
        qidoRoot: 'http://localhost:8042/dicom-web',
        stowRoot: 'http://localhost:8042/dicom-web',
        qidoSupportsIncludeField: true,
        imageRendering: 'wadors',
        thumbnailRendering: 'wadors',
        enableStudyLazyLoad: true,
        supportsFuzzyMatching: false,
        supportsWildcard: false,
        requestOptions: {
          // Basic auth header for Orthanc — matches orthanc.json credentials
          auth: 'admin:password',
        },
      },
    },
  ],
};
