// ==============================================================================
// Azure Functions (Flex Consumption / Python)
// ==============================================================================
@description('リソースのデプロイ先リージョン。')
param location string
@description('リソースに付与する共通タグ。')
param tags object
@description('Functions を統合するサブネットのリソース ID。')
param subnetFunctionsId string
@description('Private Endpoint を配置するサブネットのリソース ID。')
param subnetPrivateEndpointsId string
@description('Functions 用 Private DNS ゾーンのリソース ID。')
param privateDnsZoneFunctionsId string
@description('Functions ランタイム用 Storage アカウント名。')
param functionsStorageAccountName string
@description('データ用 Storage アカウントの名前。')
param dataStorageAccountName string
@description('データ用 Storage アカウントのリソース ID。')
param dataStorageAccountId string
@description('AI Services のエンドポイント。')
param aiServicesEndpoint string
@description('AI Services のリソース ID。')
param aiServicesId string
@description('Application Insights の接続文字列。')
param applicationInsightsConnectionString string
@description('Speech to Text の言語設定。')
param speechLanguage string

@description('Function App 名（トークン付与済み最終名）')
param functionAppName string

@description('App Service Plan 名')
param appServicePlanName string

@description('Functions の Private Endpoint 名')
param peFunctionsName string

resource funcStorageAccount 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: functionsStorageAccountName
}

resource dataStorageAccount 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: dataStorageAccountName
}

// App Service Plan (Flex Consumption)
resource appServicePlan 'Microsoft.Web/serverfarms@2024-04-01' = {
  name: appServicePlanName
  location: location
  tags: tags
  sku: {
    tier: 'FlexConsumption'
    name: 'FC1'
  }
  kind: 'functionapp'
  properties: {
    reserved: true
  }
}

// Function App
resource functionApp 'Microsoft.Web/sites@2024-04-01' = {
  name: functionAppName
  location: location
  tags: union(tags, {
    'azd-service-name': 'functions'
  })
  kind: 'functionapp,linux'
  identity: { type: 'SystemAssigned' }
  properties: {
    serverFarmId: appServicePlan.id
    virtualNetworkSubnetId: subnetFunctionsId
    publicNetworkAccess: 'Disabled'
    httpsOnly: true
    vnetRouteAllEnabled: true
    functionAppConfig: {
      deployment: {
        storage: {
          type: 'blobContainer'
          value: 'https://${functionsStorageAccountName}.blob.${az.environment().suffixes.storage}/deploymentpackage'
          authentication: {
            type: 'SystemAssignedIdentity'
          }
        }
      }
      scaleAndConcurrency: {
        maximumInstanceCount: 100
        instanceMemoryMB: 2048
      }
      runtime: {
        name: 'python'
        version: '3.11'
      }
    }
    siteConfig: {
      appSettings: [
        { name: 'AzureWebJobsStorage__accountName', value: functionsStorageAccountName }
        { name: 'AzureWebJobsStorage__credential', value: 'managedidentity' }
        { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: applicationInsightsConnectionString }
        { name: 'DataStorage__queueServiceUri', value: 'https://${dataStorageAccountName}.queue.${az.environment().suffixes.storage}' }
        { name: 'DataStorage__blobServiceUri', value: 'https://${dataStorageAccountName}.blob.${az.environment().suffixes.storage}' }
        { name: 'DATA_STORAGE_ACCOUNT_NAME', value: dataStorageAccountName }
        { name: 'DATA_STORAGE_CONTAINER_INPUT', value: 'input' }
        { name: 'DATA_STORAGE_CONTAINER_OUTPUT', value: 'output' }
        { name: 'DATA_STORAGE_CONTAINER_PROCESSED', value: 'processed' }
        { name: 'AI_SERVICES_ENDPOINT', value: aiServicesEndpoint }
        { name: 'SPEECH_LANGUAGE', value: speechLanguage }
      ]
    }
  }
}

// Private Endpoint
resource peFunctions 'Microsoft.Network/privateEndpoints@2024-01-01' = {
  name: peFunctionsName
  location: location
  tags: tags
  properties: {
    subnet: { id: subnetPrivateEndpointsId }
    privateLinkServiceConnections: [
      {
        name: 'psc-func'
        properties: {
          privateLinkServiceId: functionApp.id
          groupIds: ['sites']
        }
      }
    ]
  }
}

resource peFunctionsDns 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-01-01' = {
  parent: peFunctions
  name: 'dns-zone-group-func'
  properties: {
    privateDnsZoneConfigs: [
      { name: 'functions', properties: { privateDnsZoneId: privateDnsZoneFunctionsId } }
    ]
  }
}

// RBAC: Functions → データ Storage
resource funcDataBlobContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(dataStorageAccountId, functionApp.id, 'Storage Blob Data Contributor')
  scope: dataStorageAccount
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
    principalId: functionApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource funcDataQueueContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(dataStorageAccountId, functionApp.id, 'Storage Queue Data Contributor')
  scope: dataStorageAccount
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '974c5e8b-45b9-4653-ba55-5f855dd0fb88')
    principalId: functionApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource funcDataQueueProcessor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(dataStorageAccountId, functionApp.id, 'Storage Queue Data Message Processor')
  scope: dataStorageAccount
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '8a0f0c08-91a1-4084-bc3d-661d67233fed')
    principalId: functionApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource funcDataQueueReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(dataStorageAccountId, functionApp.id, 'Storage Queue Data Reader')
  scope: dataStorageAccount
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '19e7f393-937e-4f77-808e-94535e297925')
    principalId: functionApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// RBAC: Functions → Functions Storage (Flex Consumption MI 必須: Blob Owner / Queue Contributor / Table Contributor)
// https://learn.microsoft.com/azure/azure-functions/flex-consumption-how-to#configure-deployment-settings
resource funcHostBlobOwner 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(funcStorageAccount.id, functionApp.id, 'Storage Blob Data Owner')
  scope: funcStorageAccount
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'b7e6dc6d-f1e8-4753-8033-0f276bb0955b')
    principalId: functionApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource funcHostQueueContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(funcStorageAccount.id, functionApp.id, 'Storage Queue Data Contributor')
  scope: funcStorageAccount
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '974c5e8b-45b9-4653-ba55-5f855dd0fb88')
    principalId: functionApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource funcHostTableContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(funcStorageAccount.id, functionApp.id, 'Storage Table Data Contributor')
  scope: funcStorageAccount
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '0a9a7e1f-b9d0-4cc4-a60d-0319b160aaa3')
    principalId: functionApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource funcHostStorageAccountContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(funcStorageAccount.id, functionApp.id, 'Storage Account Contributor')
  scope: funcStorageAccount
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '17d1049b-9a84-46fb-8f53-869881c3d3ab')
    principalId: functionApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// RBAC: Functions → AI Services (Cognitive Services Speech User)
resource funcCognitiveUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(aiServicesId, functionApp.id, 'Cognitive Services Speech User')
  scope: aiServicesResource
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'f2dc8367-1007-4938-bd23-fe263f013447')
    principalId: functionApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource aiServicesResource 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' existing = {
  name: last(split(aiServicesId, '/'))
}

output functionAppName string = functionApp.name
output functionAppPrincipalId string = functionApp.identity.principalId
