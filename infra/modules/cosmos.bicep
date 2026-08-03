// ==============================================================================
// Azure Cosmos DB for NoSQL (serverless)
// ==============================================================================
@description('プロジェクト名。診断設定名に使用。')
param projectName string
@description('環境名。診断設定名に使用。')
param environment string
@description('リソースのデプロイ先リージョン。')
param location string
@description('リソースに付与する共通タグ。')
param tags object
@description('Private Endpoint を配置するサブネットのリソース ID。')
param subnetPrivateEndpointsId string
@description('Cosmos DB for NoSQL 用 Private DNS ゾーンのリソース ID。')
param privateDnsZoneCosmosId string
@description('Log Analytics ワークスペースのリソース ID。')
param logAnalyticsWorkspaceResourceId string

@description('Cosmos DB アカウント名（トークン付与済み最終名）。')
param cosmosName string
@description('Cosmos DB の Private Endpoint 名。')
param peCosmosName string
@description('Cosmos DB for NoSQL のデータベース名。')
param databaseName string = 'transcription'
@description('Cosmos DB for NoSQL のコンテナー名。')
param containerName string = 'metadata'

resource cosmos 'Microsoft.DocumentDB/databaseAccounts@2025-04-15' = {
  name: cosmosName
  location: location
  tags: tags
  kind: 'GlobalDocumentDB'
  properties: {
    databaseAccountOfferType: 'Standard'
    publicNetworkAccess: 'Disabled'
    disableLocalAuth: true
    minimalTlsVersion: 'Tls12'
    consistencyPolicy: {
      defaultConsistencyLevel: 'Session'
    }
    locations: [
      {
        locationName: location
        failoverPriority: 0
        isZoneRedundant: false
      }
    ]
    capabilities: [
      {
        name: 'EnableServerless'
      }
    ]
  }
}

resource database 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2025-04-15' = {
  parent: cosmos
  name: databaseName
  tags: tags
  properties: {
    resource: {
      id: databaseName
    }
  }
}

resource container 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2025-04-15' = {
  parent: database
  name: containerName
  tags: tags
  properties: {
    resource: {
      id: containerName
      partitionKey: {
        paths: [
          '/id'
        ]
        kind: 'Hash'
        version: 2
      }
      indexingPolicy: {
        indexingMode: 'consistent'
        automatic: true
        includedPaths: [
          {
            path: '/*'
          }
        ]
        excludedPaths: [
          {
            path: '/"_etag"/?'
          }
        ]
      }
    }
  }
}

resource peCosmos 'Microsoft.Network/privateEndpoints@2024-01-01' = {
  name: peCosmosName
  location: location
  tags: tags
  properties: {
    subnet: {
      id: subnetPrivateEndpointsId
    }
    privateLinkServiceConnections: [
      {
        name: 'psc-cosmos'
        properties: {
          privateLinkServiceId: cosmos.id
          groupIds: [
            'Sql'
          ]
        }
      }
    ]
  }
}

resource peCosmosDns 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-01-01' = {
  parent: peCosmos
  name: 'dns-zone-group-cosmos'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'cosmos'
        properties: {
          privateDnsZoneId: privateDnsZoneCosmosId
        }
      }
    ]
  }
}

resource cosmosDiagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'diag-cosmos-${projectName}-${environment}'
  scope: cosmos
  properties: {
    workspaceId: logAnalyticsWorkspaceResourceId
    logAnalyticsDestinationType: 'Dedicated'
    logs: [
      {
        categoryGroup: 'allLogs'
        enabled: true
      }
    ]
  }
}

output cosmosId string = cosmos.id
output cosmosEndpoint string = cosmos.properties.documentEndpoint
output databaseName string = database.name
output containerName string = container.name