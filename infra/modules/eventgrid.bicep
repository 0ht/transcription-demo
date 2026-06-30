// ==============================================================================
// Event Grid (Blob → Storage Queue)
// ==============================================================================
param projectName string
param environment string
param dataStorageAccountId string

resource systemTopic 'Microsoft.EventGrid/systemTopics@2024-06-01-preview' = {
  name: 'evgt-blob-${projectName}-${environment}'
  location: resourceGroup().location
  properties: {
    source: dataStorageAccountId
    topicType: 'Microsoft.Storage.StorageAccounts'
  }
}

resource eventSubscription 'Microsoft.EventGrid/systemTopics/eventSubscriptions@2024-06-01-preview' = {
  parent: systemTopic
  name: 'evgs-blob-created'
  properties: {
    eventDeliverySchema: 'EventGridSchema'
    filter: {
      includedEventTypes: ['Microsoft.Storage.BlobCreated']
      subjectBeginsWith: '/blobServices/default/containers/input/'
      advancedFilters: [
        {
          operatorType: 'StringEndsWith'
          key: 'subject'
          values: [
            '.wav', '.mp3', '.m4a', '.ogg', '.flac', '.wma'
            '.txt', '.md', '.json', '.vtt'
          ]
        }
      ]
    }
    destination: {
      endpointType: 'StorageQueue'
      properties: {
        resourceId: dataStorageAccountId
        queueName: 'blob-events'
      }
    }
    retryPolicy: {
      maxDeliveryAttempts: 3
      eventTimeToLiveInMinutes: 1440
    }
  }
}
