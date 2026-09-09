@description('リソースのデプロイ先リージョン。')
param location string
@description('Private Endpoint のデプロイ先リージョン。接続先 VNet と同じリージョンを指定。')
param privateEndpointLocation string
@description('リソースに付与する共通タグ。')
param tags object
@description('Private Endpoint を配置するサブネットのリソース ID。')
param subnetPrivateEndpointsId string
@description('Search 用 Private DNS ゾーンのリソース ID。')
param privateDnsZoneSearchId string

@description('Search service sku')
param sku string = 'basic'

@description('Search index name')
param indexName string = 'documents'

@description('Semantic configuration name')
param semanticConfigName string = 'default-semantic'

@description('Search サービス名（トークン付与済み最終名）')
param searchName string

@description('Search の Private Endpoint 名')
param peSearchName string

@description('インデクサーが blob を読むデータ用 Storage のリソース ID（shared private link 対象）。')
param dataStorageAccountId string

@description('ベクトライザー/スキルが呼ぶ AI Services のリソース ID（shared private link 対象）。')
param aiServicesId string

@description('azd 実行者の Entra ID オブジェクト ID（postprovision hook が index/indexer を作成するため）。')
param deployerPrincipalId string

@description('azd 実行者のプリンシパル種別。')
@allowed([
  'User'
  'ServicePrincipal'
  'Group'
])
param deployerPrincipalType string = 'User'

resource search 'Microsoft.Search/searchServices@2023-11-01' = {
  name: searchName
  location: location
  tags: tags
  sku: {
    name: sku
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    publicNetworkAccess: 'disabled'
    disableLocalAuth: true
    replicaCount: 1
    partitionCount: 1
    hostingMode: 'default'
  }
}

resource peSearch 'Microsoft.Network/privateEndpoints@2024-01-01' = {
  name: peSearchName
  location: privateEndpointLocation
  tags: tags
  properties: {
    subnet: {
      id: subnetPrivateEndpointsId
    }
    privateLinkServiceConnections: [
      {
        name: 'psc-search'
        properties: {
          privateLinkServiceId: search.id
          groupIds: [
            'searchService'
          ]
        }
      }
    ]
  }
}

resource peSearchDns 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-01-01' = {
  parent: peSearch
  name: 'dns-zone-group-search'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'search'
        properties: {
          privateDnsZoneId: privateDnsZoneSearchId
        }
      }
    ]
  }
}

// Shared Private Link: Search → データ Storage (blob)。インデクサーが閉域の blob を読むための送信経路。
// 作成後、対象 Storage 側の private endpoint connection を承認する必要がある（postprovision hook）。
resource splBlob 'Microsoft.Search/searchServices/sharedPrivateLinkResources@2023-11-01' = {
  parent: search
  name: 'spl-blob'
  properties: {
    privateLinkResourceId: dataStorageAccountId
    groupId: 'blob'
    requestMessage: 'azd: Azure AI Search indexer blob access'
  }
}

// Shared Private Link: Search → AI Services (openai_account)。スキル/ベクトライザーが OpenAI を呼ぶ送信経路。
// SPL はアカウントごとに直列作成が安全なため blob の後に作成する。
resource splOpenAI 'Microsoft.Search/searchServices/sharedPrivateLinkResources@2023-11-01' = {
  parent: search
  name: 'spl-openai'
  properties: {
    privateLinkResourceId: aiServicesId
    groupId: 'openai_account'
    requestMessage: 'azd: Azure AI Search vectorizer OpenAI access'
  }
  dependsOn: [
    splBlob
  ]
}

// RBAC: Search MI → データ Storage (Storage Blob Data Reader) — インデクサーの blob 読取用
resource dataStorageResource 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: last(split(dataStorageAccountId, '/'))
}

resource searchBlobReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(dataStorageAccountId, search.id, 'Storage Blob Data Reader')
  scope: dataStorageResource
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1')
    principalId: search.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// RBAC: azd 実行者 → Search (Search Service Contributor) — hook が index/datasource/skillset/indexer を
// データプレーンで作成するため（disableLocalAuth=true なので Entra ロール必須）
resource deployerSearchContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(search.id, deployerPrincipalId, 'Search Service Contributor')
  scope: search
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7ca78c08-252a-4471-8644-bb5ff32d4ba0')
    principalId: deployerPrincipalId
    principalType: deployerPrincipalType
  }
}

// RBAC: azd 実行者 → Search (Search Index Data Contributor) — 検証・手動投入用
resource deployerSearchDataContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(search.id, deployerPrincipalId, 'Search Index Data Contributor')
  scope: search
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '8ebe5a00-799e-43f5-93ac-243d3dce84a7')
    principalId: deployerPrincipalId
    principalType: deployerPrincipalType
  }
}

output searchServiceName string = search.name
output searchEndpoint string = 'https://${search.name}.search.windows.net'
output searchId string = search.id
output searchPrincipalId string = search.identity.principalId
output indexName string = indexName
output semanticConfigName string = semanticConfigName
