@description('AI Services / Foundry Account 名。')
param aiServicesName string

@description('Foundry Project 名。')
param foundryProjectName string

@description('Agent subnet のリソース ID。')
param agentSubnetId string

@description('Storage Account 名。')
param storageAccountName string

@description('Azure AI Search 名。')
param searchServiceName string

@description('Cosmos DB Account 名。')
param cosmosAccountName string

@description('Application Insights 名。')
param appInsightsName string

@description('既存 account に capability host がなく、明示作成したい場合 true。')
param createAccountCapabilityHost bool = false

@description('Account-level capability host 名。')
param accountCapabilityHostName string = '${aiServicesName}@aml_aiagentservice'

@description('Foundry Project の workspace ID。条件付き Storage/Cosmos RBAC が不要なら空文字。')
param projectWorkspaceId string = ''

@description('Application Insights に割り当てる role definition GUID 一覧。')
param appInsightsRoleDefinitionGuids array = [
  '73c42c96-874c-492b-b04d-ab87d138a893' // Log Analytics Reader
  'dbc9c667-e97f-4491-aee6-90b9cf960190' // Privileged Monitoring Data Reader
]

resource aiServices 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' existing = {
  name: aiServicesName
}

resource foundryProject 'Microsoft.CognitiveServices/accounts/projects@2025-04-01-preview' existing = {
  parent: aiServices
  name: foundryProjectName
}

resource searchService 'Microsoft.Search/searchServices@2024-06-01-preview' existing = {
  name: searchServiceName
}

resource storageAccount 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: storageAccountName
}

resource cosmosAccount 'Microsoft.DocumentDB/databaseAccounts@2024-12-01-preview' existing = {
  name: cosmosAccountName
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' existing = {
  name: appInsightsName
}

var projectPrincipalId = foundryProject.identity.principalId

resource accountCapabilityHost 'Microsoft.CognitiveServices/accounts/capabilityHosts@2025-04-01-preview' = if (createAccountCapabilityHost) {
  parent: aiServices
  name: accountCapabilityHostName
  properties: {
    #disable-next-line BCP037
    capabilityHostKind: 'Agents'
    #disable-next-line BCP037
    customerSubnet: agentSubnetId
  }
}

resource searchIndexDataContributorAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: searchService
  name: guid(foundryProject.id, searchService.id, 'SearchIndexDataContributor')
  properties: {
    principalId: projectPrincipalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '8ebe5a00-799e-43f5-93ac-243d3dce84a7')
    principalType: 'ServicePrincipal'
  }
}

resource searchServiceContributorAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: searchService
  name: guid(foundryProject.id, searchService.id, 'SearchServiceContributor')
  properties: {
    principalId: projectPrincipalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7ca78c08-252a-4471-8644-bb5ff32d4ba0')
    principalType: 'ServicePrincipal'
  }
}

resource storageAccountContributorAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: storageAccount
  name: guid(foundryProject.id, storageAccount.id, 'StorageAccountContributor')
  properties: {
    principalId: projectPrincipalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '17d1049b-9a84-46fb-8f53-869881c3d3ab')
    principalType: 'ServicePrincipal'
  }
}

resource storageBlobDataContributorAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: storageAccount
  name: guid(foundryProject.id, storageAccount.id, 'StorageBlobDataContributor')
  properties: {
    principalId: projectPrincipalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
    principalType: 'ServicePrincipal'
  }
}

resource storageQueueDataContributorAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: storageAccount
  name: guid(foundryProject.id, storageAccount.id, 'StorageQueueDataContributor')
  properties: {
    principalId: projectPrincipalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '974c5e8b-45b9-4653-ba55-5f855dd0fb88')
    principalType: 'ServicePrincipal'
  }
}

var storageBlobOwnerCondition = '((!(ActionMatches{\'Microsoft.Storage/storageAccounts/blobServices/containers/blobs/tags/read\'}) AND !(ActionMatches{\'Microsoft.Storage/storageAccounts/blobServices/containers/blobs/filter/action\'}) AND !(ActionMatches{\'Microsoft.Storage/storageAccounts/blobServices/containers/blobs/tags/write\'})) OR (@Resource[Microsoft.Storage/storageAccounts/blobServices/containers:name] StringStartsWithIgnoreCase \'${projectWorkspaceId}\' AND @Resource[Microsoft.Storage/storageAccounts/blobServices/containers:name] StringLikeIgnoreCase \'*-azureml-agent\'))'

resource storageBlobDataOwnerAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(projectWorkspaceId)) {
  scope: storageAccount
  name: guid(foundryProject.id, storageAccount.id, projectWorkspaceId, 'StorageBlobDataOwner')
  properties: {
    principalId: projectPrincipalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'b7e6dc6d-f1e8-4753-8033-0f276bb0955b')
    principalType: 'ServicePrincipal'
    conditionVersion: '2.0'
    condition: storageBlobOwnerCondition
  }
}

resource cosmosDbOperatorAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: cosmosAccount
  name: guid(foundryProject.id, cosmosAccount.id, 'CosmosDbOperator')
  properties: {
    principalId: projectPrincipalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '230815da-be43-4aae-9cb4-875f7bd000aa')
    principalType: 'ServicePrincipal'
  }
}

var cosmosSqlRoleDefinitionId = resourceId(
  'Microsoft.DocumentDB/databaseAccounts/sqlRoleDefinitions',
  cosmosAccountName,
  '00000000-0000-0000-0000-000000000002'
)
var cosmosSqlScope = '/subscriptions/${subscription().subscriptionId}/resourceGroups/${resourceGroup().name}/providers/Microsoft.DocumentDB/databaseAccounts/${cosmosAccountName}/dbs/enterprise_memory'

resource cosmosSqlDataContributorAssignment 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2022-05-15' = if (!empty(projectWorkspaceId)) {
  parent: cosmosAccount
  name: guid(foundryProject.id, projectWorkspaceId, cosmosAccountName, 'CosmosSqlDataContributor')
  properties: {
    principalId: projectPrincipalId
    roleDefinitionId: cosmosSqlRoleDefinitionId
    scope: cosmosSqlScope
  }
}

resource appInsightsRoleAssignments 'Microsoft.Authorization/roleAssignments@2022-04-01' = [
  for roleGuid in appInsightsRoleDefinitionGuids: {
    scope: appInsights
    name: guid(foundryProject.id, roleGuid, appInsights.id, 'AppInsightsRole')
    properties: {
      principalId: projectPrincipalId
      roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleGuid)
      principalType: 'ServicePrincipal'
    }
  }
]

output projectPrincipalId string = projectPrincipalId
output accountCapabilityHostName string = createAccountCapabilityHost ? accountCapabilityHost.name : ''
output projectId string = foundryProject.id
