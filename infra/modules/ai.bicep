// ==============================================================================
// AI Services (Foundry リソース) + Foundry Project
// ==============================================================================
param projectName string
param environment string
param location string
param tags object
param subnetPrivateEndpointsId string
param privateDnsZoneCognitiveId string
param dataStorageAccountId string

resource aiServices 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' = {
  name: 'ais-${projectName}-${environment}'
  location: location
  tags: tags
  kind: 'AIServices'
  sku: { name: 'S0' }
  identity: { type: 'SystemAssigned' }
  properties: {
    allowProjectManagement: true
    customSubDomainName: 'ais-${projectName}-${environment}'
    publicNetworkAccess: 'Disabled'
    disableLocalAuth: true
    networkAcls: {
      defaultAction: 'Deny'
    }
  }
}

// Foundry Project (Hub 不要のスタンドアロン構成)
resource foundryProject 'Microsoft.CognitiveServices/accounts/projects@2025-04-01-preview' = {
  parent: aiServices
  name: 'proj-${projectName}-${environment}'
  location: location
  tags: tags
  identity: { type: 'SystemAssigned' }
  properties: {}
}

// AI Services の Private Endpoint
resource peAiServices 'Microsoft.Network/privateEndpoints@2024-01-01' = {
  name: 'pe-ais-${environment}'
  location: location
  tags: tags
  properties: {
    subnet: { id: subnetPrivateEndpointsId }
    privateLinkServiceConnections: [
      {
        name: 'psc-ais'
        properties: {
          privateLinkServiceId: aiServices.id
          groupIds: ['account']
        }
      }
    ]
  }
}

resource peAiServicesDns 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-01-01' = {
  parent: peAiServices
  name: 'dns-zone-group-cognitive'
  properties: {
    privateDnsZoneConfigs: [
      { name: 'cognitive', properties: { privateDnsZoneId: privateDnsZoneCognitiveId } }
    ]
  }
}

// RBAC: AI Services → データ Storage (Batch Transcription の入出力に必要)
resource aiStorageBlobReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(dataStorageAccountId, aiServices.id, 'Storage Blob Data Reader')
  scope: dataStorageResource
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1')
    principalId: aiServices.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource dataStorageResource 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: last(split(dataStorageAccountId, '/'))
}

output aiServicesEndpoint string = aiServices.properties.endpoint
output aiServicesId string = aiServices.id
output aiServicesPrincipalId string = aiServices.identity.principalId
