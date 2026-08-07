// ==============================================================================
// Blob 文字起こしシステム — メインテンプレート
// azd provision で呼び出されるエントリポイント
// ==============================================================================
targetScope = 'subscription'

@description('環境名 (dev / stg / prod)')
param environmentName string

@description('Azure リージョン')
param location string

@description('プロジェクト名。リソース名のプレフィックスに使用。')
param projectName string = 'transcription'

@description('リソースに付与する project タグの値。')
param projectTag string = 'blob-transcription'

@description('Speech to Text の言語設定')
param speechLanguage string = 'ja-JP'

@description('UI アクセスを許可する社内 IP レンジ（CIDR 形式）')
param allowedIpRanges array = []

// azd が自動設定するパラメータ
#disable-next-line no-unused-params
param principalId string = ''

@description('UI Container App が既存リソースとして存在するか。azd が SERVICE_UI_RESOURCE_EXISTS から自動セット。初回 false→公開イメージで作成、2回目以降 true→既存 ACR イメージを引き継ぐ。')
param uiExists bool = false

var tags = {
  'azd-env-name': environmentName
  project: projectTag
  managed_by: 'bicep'
}

// ------------------------------------------------------------------------------
// リソース名の一意性トークン
// グローバル一意性が必要なリソース（Storage / ACR / AI Services / Search / Function App）
// の名前末尾に常に付与し、サブスクリプション・環境・リージョンをまたいだ名前衝突を防ぐ。
// 全環境（dev を含む）・全リソースにトークンを付与する。
// ------------------------------------------------------------------------------
var resourceToken = substring(uniqueString(subscription().subscriptionId, environmentName, location), 0, 6)

// ------------------------------------------------------------------------------
// リソース名の集中管理: projectName / environmentName / トークンから標準命名規則で導出する（変数）。
// グローバル一意リソース（Storage x2 / ACR / AI Services / Search / Function App）のみ末尾にトークンを付与。
// ------------------------------------------------------------------------------
var projClean = replace(projectName, '-', '')
var envClean = toLower(replace(environmentName, '-', ''))

var rgName = 'rg-${projectName}-${environmentName}'
var vnetName = 'vnet-${projectName}-${environmentName}'
var subnetFunctionsName = 'snet-functions'
var subnetAcaName = 'snet-aca'
var subnetPrivateEndpointsName = 'snet-privateendpoints'
var subnetAgentName = 'snet-agent'
var logAnalyticsName = 'log-${projectName}-${environmentName}'
var appInsightsName = 'appi-${projectName}-${environmentName}'
var amplsName = 'ampls-${projectName}-${environmentName}'
var peMonitorName = 'pe-monitor-${environmentName}'
var eventGridTopicName = 'evgt-blob-${projectName}-${environmentName}'
var eventGridSubscriptionName = 'evgs-blob-created'
var containerAppEnvName = 'cae-${projectName}-${environmentName}'
var containerAppName = 'ca-${projectName}-ui-${environmentName}'
var appServicePlanName = 'asp-${projectName}-${environmentName}'
var functionAppName = 'func-${projectName}-${environmentName}-${resourceToken}'
var peFunctionsName = 'pe-func-${environmentName}'
var dataStorageName = 'st${take(projClean, 5)}${take(envClean, 6)}d${resourceToken}'
var functionsStorageName = 'st${take(projClean, 5)}${take(envClean, 6)}f${resourceToken}'
var peDataBlobName = 'pe-st-data-${environmentName}'
var peDataQueueName = 'pe-st-data-queue-${environmentName}'
var peFuncStorageName = 'pe-st-func-${environmentName}'
var peFuncStorageTableName = 'pe-st-func-table-${environmentName}'
var acrName = 'acr${projClean}${envClean}${resourceToken}'
var peAcrName = 'pe-acr-${environmentName}'
var aiServicesName = 'ais-${projectName}-${environmentName}-${resourceToken}'
var foundryProjectName = 'proj-${projectName}-${environmentName}'
var peAiServicesName = 'pe-ais-${environmentName}'
var searchName = 'srch-${projectName}-${environmentName}-${resourceToken}'
var peSearchName = 'pe-srch-${environmentName}'
var cosmosName = 'cosmos-${take(projClean, 15)}-${take(envClean, 6)}-${resourceToken}'
var peCosmosName = 'pe-cosmos-${environmentName}'

resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: rgName
  location: location
  tags: tags
}

module network 'modules/network.bicep' = {
  name: 'network'
  scope: rg
  params: {
    location: location
    tags: tags
    vnetName: vnetName
    subnetFunctionsName: subnetFunctionsName
    subnetAcaName: subnetAcaName
    subnetPrivateEndpointsName: subnetPrivateEndpointsName
    subnetAgentName: subnetAgentName
  }
}

// AI Services のリソース ID を予測（Trusted Service として Storage の networkAcls.resourceAccessRules に登録するため）
// ai.bicep 側の命名と一致させる必要があるため、同じ最終名を使う。
var aiServicesResourceId = resourceId(subscription().subscriptionId, rg.name, 'Microsoft.CognitiveServices/accounts', aiServicesName)

module storage 'modules/storage.bicep' = {
  name: 'storage'
  scope: rg
  params: {
    location: location
    tags: tags
    subnetPrivateEndpointsId: network.outputs.subnetPrivateEndpointsId
    privateDnsZoneBlobId: network.outputs.privateDnsZoneBlobId
    privateDnsZoneQueueId: network.outputs.privateDnsZoneQueueId
    privateDnsZoneTableId: network.outputs.privateDnsZoneTableId
    aiServicesResourceId: aiServicesResourceId
    dataStorageName: dataStorageName
    functionsStorageName: functionsStorageName
    peDataBlobName: peDataBlobName
    peDataQueueName: peDataQueueName
    peFuncStorageName: peFuncStorageName
    peFuncStorageTableName: peFuncStorageTableName
  }
}

module acr 'modules/acr.bicep' = {
  name: 'acr'
  scope: rg
  params: {
    location: location
    tags: tags
    subnetPrivateEndpointsId: network.outputs.subnetPrivateEndpointsId
    privateDnsZoneAcrId: network.outputs.privateDnsZoneAcrId
    acrName: acrName
    peAcrName: peAcrName
  }
}

module ai 'modules/ai.bicep' = {
  name: 'ai'
  scope: rg
  params: {
    location: location
    tags: tags
    subnetPrivateEndpointsId: network.outputs.subnetPrivateEndpointsId
    subnetAgentId: network.outputs.subnetAgentId
    privateDnsZoneCognitiveId: network.outputs.privateDnsZoneCognitiveId
    privateDnsZoneOpenAIId: network.outputs.privateDnsZoneOpenAIId
    privateDnsZoneServicesAiId: network.outputs.privateDnsZoneServicesAiId
    dataStorageAccountId: storage.outputs.dataStorageAccountId
    aiServicesName: aiServicesName
    foundryProjectName: foundryProjectName
    peAiServicesName: peAiServicesName
  }
}

module monitoring 'modules/monitoring.bicep' = {
  name: 'monitoring'
  scope: rg
  params: {
    location: location
    tags: tags
    subnetPrivateEndpointsId: network.outputs.subnetPrivateEndpointsId
    privateDnsZoneMonitorId: network.outputs.privateDnsZoneMonitorId
    privateDnsZoneOmsId: network.outputs.privateDnsZoneOmsId
    privateDnsZoneOdsId: network.outputs.privateDnsZoneOdsId
    privateDnsZoneAgentsvcId: network.outputs.privateDnsZoneAgentsvcId
    privateDnsZoneBlobId: network.outputs.privateDnsZoneBlobId
    logAnalyticsName: logAnalyticsName
    appInsightsName: appInsightsName
    amplsName: amplsName
    peMonitorName: peMonitorName
  }
}

module cosmos 'modules/cosmos.bicep' = {
  name: 'cosmos'
  scope: rg
  params: {
    projectName: projectName
    environment: environmentName
    location: location
    tags: tags
    subnetPrivateEndpointsId: network.outputs.subnetPrivateEndpointsId
    privateDnsZoneCosmosId: network.outputs.privateDnsZoneCosmosId
    logAnalyticsWorkspaceResourceId: monitoring.outputs.logAnalyticsWorkspaceResourceId
    cosmosName: cosmosName
    peCosmosName: peCosmosName
  }
}

module functions 'modules/functions.bicep' = {
  name: 'functions'
  scope: rg
  params: {
    location: location
    tags: tags
    subnetFunctionsId: network.outputs.subnetFunctionsId
    subnetPrivateEndpointsId: network.outputs.subnetPrivateEndpointsId
    privateDnsZoneFunctionsId: network.outputs.privateDnsZoneFunctionsId
    functionsStorageAccountName: storage.outputs.functionsStorageAccountName
    dataStorageAccountName: storage.outputs.dataStorageAccountName
    dataStorageAccountId: storage.outputs.dataStorageAccountId
    aiServicesEndpoint: ai.outputs.aiServicesEndpoint
    aiServicesId: ai.outputs.aiServicesId
    applicationInsightsConnectionString: monitoring.outputs.applicationInsightsConnectionString
    speechLanguage: speechLanguage
    functionAppName: functionAppName
    appServicePlanName: appServicePlanName
    peFunctionsName: peFunctionsName
  }
}

module eventgrid 'modules/eventgrid.bicep' = {
  name: 'eventgrid'
  scope: rg
  params: {
    dataStorageAccountId: storage.outputs.dataStorageAccountId
    eventGridTopicName: eventGridTopicName
    eventGridSubscriptionName: eventGridSubscriptionName
  }
}

module containerApps 'modules/container-apps.bicep' = {  
  name: 'containerApps'  
  scope: rg  
  params: {  
    environment: environmentName  
    location: location  
    tags: tags  
    subnetAcaId: network.outputs.subnetAcaId  
    logAnalyticsWorkspaceId: monitoring.outputs.logAnalyticsWorkspaceId  
    logAnalyticsWorkspaceResourceId: monitoring.outputs.logAnalyticsWorkspaceResourceId  
    dataStorageAccountName: storage.outputs.dataStorageAccountName  
    dataStorageAccountId: storage.outputs.dataStorageAccountId  
    acrLoginServer: acr.outputs.acrLoginServer  
    acrId: acr.outputs.acrId  
    allowedIpRanges: allowedIpRanges  
    uiExists: uiExists  
    containerAppEnvName: containerAppEnvName  
    containerAppName: containerAppName  
  
    azureOpenAIEndpoint: ai.outputs.openAIEndpoint  
    azureOpenAIId: ai.outputs.openAIId  
    azureOpenAIChatDeployment: ai.outputs.chatDeployment  
    azureOpenAIEmbeddingDeployment: ai.outputs.embeddingDeployment  
    azureOpenAIApiVersion: '2025-04-01-preview'  
  
    azureSearchEndpoint: search.outputs.searchEndpoint  
    azureSearchId: search.outputs.searchId  
    azureSearchPrincipalId: search.outputs.searchPrincipalId  
    azureSearchIndexName: search.outputs.indexName  
    azureSearchSemanticConfig: search.outputs.semanticConfigName  
    readFields: 'content,source_file,transcript_path,chunk_id,speaker,start_time'  
  }  
}  

module search 'modules/search.bicep' = {  
  name: 'search'  
  scope: rg  
  params: {  
    location: location  
    tags: tags  
    subnetPrivateEndpointsId: network.outputs.subnetPrivateEndpointsId  
    privateDnsZoneSearchId: network.outputs.privateDnsZoneSearchId  
    searchName: searchName  
    peSearchName: peSearchName  
  }  
}  

module foundryAgentProject 'modules/foundry-agent-project.bicep' = {
  name: 'foundry-agent-project'
  scope: rg
  params: {
    aiServicesName: aiServicesName
    foundryProjectName: foundryProjectName
    agentSubnetId: network.outputs.subnetAgentId
    storageAccountName: storage.outputs.dataStorageAccountName
    searchServiceName: search.outputs.searchServiceName
    cosmosAccountName: cosmosName
    appInsightsName: monitoring.outputs.applicationInsightsName
  }
  dependsOn: [
    ai
    cosmos
  ]
}

// ==============================================================================
// azd 用出力
// ==============================================================================
output AZURE_RESOURCE_GROUP string = rg.name
output AZURE_CONTAINER_REGISTRY_ENDPOINT string = acr.outputs.acrLoginServer
output AZURE_CONTAINER_REGISTRY_NAME string = acr.outputs.acrName
output AZURE_FUNCTIONS_STORAGE_ACCOUNT_NAME string = storage.outputs.functionsStorageAccountName
output SERVICE_FUNCTIONS_RESOURCE_NAME string = functions.outputs.functionAppName
output SERVICE_UI_RESOURCE_NAME string = containerApps.outputs.containerAppName
output AZURE_CONTAINER_APP_ENVIRONMENT_NAME string = containerApps.outputs.containerAppEnvironmentName
output AI_SERVICES_ENDPOINT string = ai.outputs.aiServicesEndpoint
output CONTAINER_APP_URL string = containerApps.outputs.containerAppUrl
output AZURE_OPENAI_ENDPOINT string = ai.outputs.openAIEndpoint  
output AZURE_OPENAI_CHAT_DEPLOYMENT string = ai.outputs.chatDeployment  
output AZURE_SEARCH_ENDPOINT string = search.outputs.searchEndpoint  
output AZURE_SEARCH_INDEX_NAME string = search.outputs.indexName  
output AZURE_COSMOS_ENDPOINT string = cosmos.outputs.cosmosEndpoint
output AZURE_COSMOS_DATABASE_NAME string = cosmos.outputs.databaseName
output AZURE_COSMOS_CONTAINER_NAME string = cosmos.outputs.containerName
