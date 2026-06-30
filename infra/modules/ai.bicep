// ==============================================================================
// AI Services (Foundry リソース) + Foundry Project
// ==============================================================================
param projectName string
param environment string
param location string
param tags object
param subnetPrivateEndpointsId string
param privateDnsZoneCognitiveId string
param privateDnsZoneOpenAIId string
param dataStorageAccountId string

@description('Chat model deployment name')
param chatDeploymentName string = 'gpt-4.1-mini'

@description('Chat model name')
param chatModelName string = 'gpt-4.1-mini'

@description('Chat model version')
param chatModelVersion string = '2025-04-14'

@description('Embedding deployment name')
param embeddingDeploymentName string = 'text-embedding-3-large'

@description('Embedding model name')
param embeddingModelName string = 'text-embedding-3-large'

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

// OpenAI モデルデプロイ（AIServices アカウント上に直接配置）
resource chatDeployment 'Microsoft.CognitiveServices/accounts/deployments@2025-04-01-preview' = {
  parent: aiServices
  name: chatDeploymentName
  sku: {
    name: 'GlobalStandard'
    capacity: 10
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: chatModelName
      version: chatModelVersion
    }
    versionUpgradeOption: 'OnceNewDefaultVersionAvailable'
  }
}

resource embeddingDeployment 'Microsoft.CognitiveServices/accounts/deployments@2025-04-01-preview' = {
  parent: aiServices
  name: embeddingDeploymentName
  sku: {
    name: 'Standard'
    capacity: 10
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: embeddingModelName
      version: '1'
    }
    versionUpgradeOption: 'OnceNewDefaultVersionAvailable'
  }
  dependsOn: [
    chatDeployment
  ]
}

// AI Services の Private Endpoint
// 注意: モデルデプロイ完了後に作成する。アカウントへの PUT（モデルデプロイ含む）は
// 非同期で一時的に "Accepted" 状態になり、その最中に PE がアカウントを参照すると
// AccountProvisioningStateInvalid で失敗するため、明示的に dependsOn で直列化する。
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
  dependsOn: [
    chatDeployment
    embeddingDeployment
  ]
}

resource peAiServicesDns 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-01-01' = {
  parent: peAiServices
  name: 'dns-zone-group-cognitive'
  properties: {
    privateDnsZoneConfigs: [
      { name: 'cognitive', properties: { privateDnsZoneId: privateDnsZoneCognitiveId } }
      { name: 'openai', properties: { privateDnsZoneId: privateDnsZoneOpenAIId } }
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

// OpenAI 互換エンドポイント・デプロイ名（旧 openai モジュール出力を置換）
output openAIEndpoint string = 'https://${aiServices.name}.openai.azure.com/'
output openAIId string = aiServices.id
output chatDeployment string = chatDeployment.name
output embeddingDeployment string = embeddingDeployment.name
