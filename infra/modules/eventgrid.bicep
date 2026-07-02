// ==============================================================================
// Event Grid (Blob → Storage Queue)
// ==============================================================================
@description('イベントソースとなるデータ用 Storage アカウントのリソース ID。')
param dataStorageAccountId string

@description('Event Grid System Topic 名')
param eventGridTopicName string

@description('Event Grid Event Subscription 名')
param eventGridSubscriptionName string

resource systemTopic 'Microsoft.EventGrid/systemTopics@2024-06-01-preview' = {
  name: eventGridTopicName
  location: resourceGroup().location
  properties: {
    source: dataStorageAccountId
    topicType: 'Microsoft.Storage.StorageAccounts'
  }
}

resource eventSubscription 'Microsoft.EventGrid/systemTopics/eventSubscriptions@2024-06-01-preview' = {
  parent: systemTopic
  name: eventGridSubscriptionName
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
