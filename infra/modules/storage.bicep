// ==============================================================================
// Storage Accounts (データ用 + Functions ランタイム用)
// ==============================================================================
param projectName string
param environment string
param location string
param tags object
param subnetPrivateEndpointsId string
param privateDnsZoneBlobId string
param privateDnsZoneQueueId string
param privateDnsZoneTableId string
@description('AI Services リソース ID。Trusted Service として Storage の resourceAccessRules に登録。空文字なら登録しない。')
param aiServicesResourceId string = ''

// データ用 Storage Account
resource dataStorage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: 'st${replace(projectName, '-', '')}data${environment}'
  location: location
  tags: tags
  kind: 'StorageV2'
  sku: { name: 'Standard_LRS' }
  identity: { type: 'SystemAssigned' }
  properties: {
    minimumTlsVersion: 'TLS1_2'
    // 閉域化：パブリックエンドポイントは Enabled だが defaultAction=Deny により外部からの直接アクセスは不可。
    // Speech (Batch Transcription) は AI Services リソース ID を Trusted Service として登録（resourceAccessRules）し、
    // Speech バックエンドが当該 AI Services 経由で発行されたジョブのみ Storage アクセスを許可する。
    // ※ ストレージ側を publicNetworkAccess=Disabled にすると Trusted Access (resourceAccessRules) が機能せず
    //   `InvalidData: The recordings URI contains invalid data.` で失敗するため Enabled 必須。
    // CLI からテストアップロードする際は手動で一時 IP 許可を追加する（deploy-guide.md セクション 9 参照）。
    publicNetworkAccess: 'Enabled'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    networkAcls: {
      defaultAction: 'Deny'
      bypass: 'AzureServices'
      resourceAccessRules: empty(aiServicesResourceId) ? [] : [
        {
          tenantId: subscription().tenantId
          resourceId: aiServicesResourceId
        }
      ]
    }
  }
}

resource containerInput 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  name: '${dataStorage.name}/default/input'
  properties: { publicAccess: 'None' }
}

resource containerProcessed 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  name: '${dataStorage.name}/default/processed'
  properties: { publicAccess: 'None' }
}

resource containerOutput 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  name: '${dataStorage.name}/default/output'
  properties: { publicAccess: 'None' }
}

// Event Grid 用 Storage Queue（ARM 経由で作成 — 閉域対応）
resource queueService 'Microsoft.Storage/storageAccounts/queueServices@2023-05-01' = {
  parent: dataStorage
  name: 'default'
}

resource blobEventsQueue 'Microsoft.Storage/storageAccounts/queueServices/queues@2023-05-01' = {
  parent: queueService
  name: 'blob-events'
}

// データ Storage の Private Endpoint (Blob)
resource peDataBlob 'Microsoft.Network/privateEndpoints@2024-01-01' = {
  name: 'pe-st-data-${environment}'
  location: location
  tags: tags
  properties: {
    subnet: { id: subnetPrivateEndpointsId }
    privateLinkServiceConnections: [
      {
        name: 'psc-st-data'
        properties: {
          privateLinkServiceId: dataStorage.id
          groupIds: ['blob']
        }
      }
    ]
  }
}

resource peDataBlobDns 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-01-01' = {
  parent: peDataBlob
  name: 'dns-zone-group-blob'
  properties: {
    privateDnsZoneConfigs: [
      { name: 'blob', properties: { privateDnsZoneId: privateDnsZoneBlobId } }
    ]
  }
}

// データ Storage の Private Endpoint (Queue)
resource peDataQueue 'Microsoft.Network/privateEndpoints@2024-01-01' = {
  name: 'pe-st-data-queue-${environment}'
  location: location
  tags: tags
  properties: {
    subnet: { id: subnetPrivateEndpointsId }
    privateLinkServiceConnections: [
      {
        name: 'psc-st-data-queue'
        properties: {
          privateLinkServiceId: dataStorage.id
          groupIds: ['queue']
        }
      }
    ]
  }
}

resource peDataQueueDns 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-01-01' = {
  parent: peDataQueue
  name: 'dns-zone-group-queue'
  properties: {
    privateDnsZoneConfigs: [
      { name: 'queue', properties: { privateDnsZoneId: privateDnsZoneQueueId } }
    ]
  }
}

// Functions ランタイム用 Storage Account
resource functionsStorage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: 'st${replace(projectName, '-', '')}func${environment}'
  location: location
  tags: tags
  kind: 'StorageV2'
  sku: { name: 'Standard_LRS' }
  identity: { type: 'SystemAssigned' }
  properties: {
    minimumTlsVersion: 'TLS1_2'
    // 閉域化：公開アクセスは完全無効化。Functions ランタイムは Private Endpoint 経由。
    // azd deploy 時のみ predeploy/postdeploy hooks で Enabled/Disabled を切り替え（azure.yaml 参照）。
    publicNetworkAccess: 'Disabled'
    allowBlobPublicAccess: false
    networkAcls: {
      defaultAction: 'Deny'
      bypass: 'AzureServices'
    }
  }
}

resource deploymentPackageContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  name: '${functionsStorage.name}/default/deploymentpackage'
  properties: { publicAccess: 'None' }
}

resource peFuncStorage 'Microsoft.Network/privateEndpoints@2024-01-01' = {
  name: 'pe-st-func-${environment}'
  location: location
  tags: tags
  properties: {
    subnet: { id: subnetPrivateEndpointsId }
    privateLinkServiceConnections: [
      {
        name: 'psc-st-func'
        properties: {
          privateLinkServiceId: functionsStorage.id
          groupIds: ['blob']
        }
      }
    ]
  }
}

resource peFuncStorageDns 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-01-01' = {
  parent: peFuncStorage
  name: 'dns-zone-group-blob-func'
  properties: {
    privateDnsZoneConfigs: [
      { name: 'blob', properties: { privateDnsZoneId: privateDnsZoneBlobId } }
    ]
  }
}

// Functions Storage の Private Endpoint (Table) — Flex Consumption ランタイムに必須
resource peFuncStorageTable 'Microsoft.Network/privateEndpoints@2024-01-01' = {
  name: 'pe-st-func-table-${environment}'
  location: location
  tags: tags
  properties: {
    subnet: { id: subnetPrivateEndpointsId }
    privateLinkServiceConnections: [
      {
        name: 'psc-st-func-table'
        properties: {
          privateLinkServiceId: functionsStorage.id
          groupIds: ['table']
        }
      }
    ]
  }
}

resource peFuncStorageTableDns 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-01-01' = {
  parent: peFuncStorageTable
  name: 'dns-zone-group-table-func'
  properties: {
    privateDnsZoneConfigs: [
      { name: 'table', properties: { privateDnsZoneId: privateDnsZoneTableId } }
    ]
  }
}

// Outputs
output dataStorageAccountName string = dataStorage.name
output dataStorageAccountId string = dataStorage.id
output functionsStorageAccountName string = functionsStorage.name
output functionsStorageAccountId string = functionsStorage.id
