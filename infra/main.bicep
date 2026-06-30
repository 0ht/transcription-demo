// ==============================================================================
// Blob 文字起こしシステム — メインテンプレート
// azd provision で呼び出されるエントリポイント
// ==============================================================================
targetScope = 'subscription'

@description('環境名 (dev / stg / prod)')
param environmentName string

@description('Azure リージョン')
param location string

@description('Speech to Text の言語設定')
param speechLanguage string = 'ja-JP'

@description('UI アクセスを許可する社内 IP レンジ（CIDR 形式）')
param allowedIpRanges array = []

// azd が自動設定するパラメータ
#disable-next-line no-unused-params
param principalId string = ''

@description('UI Container App が既存リソースとして存在するか。azd が SERVICE_UI_RESOURCE_EXISTS から自動セット。初回 false→公開イメージで作成、2回目以降 true→既存 ACR イメージを引き継ぐ。')
param uiExists bool = false

var projectName = 'transcription'
var tags = {
  'azd-env-name': environmentName
  project: 'blob-transcription'
  managed_by: 'bicep'
}
var resourceGroupName = 'rg-${projectName}-${environmentName}'

resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: resourceGroupName
  location: location
  tags: tags
}

module network 'modules/network.bicep' = {
  name: 'network'
  scope: rg
  params: {
    projectName: projectName
    environment: environmentName
    location: location
    tags: tags
  }
}

// AI Services のリソース ID を予測（Trusted Service として Storage の networkAcls.resourceAccessRules に登録するため）
var aiServicesName = 'ais-${projectName}-${environmentName}'
var aiServicesResourceId = resourceId(subscription().subscriptionId, rg.name, 'Microsoft.CognitiveServices/accounts', aiServicesName)

module storage 'modules/storage.bicep' = {
  name: 'storage'
  scope: rg
  params: {
    projectName: projectName
    environment: environmentName
    location: location
    tags: tags
    subnetPrivateEndpointsId: network.outputs.subnetPrivateEndpointsId
    privateDnsZoneBlobId: network.outputs.privateDnsZoneBlobId
    privateDnsZoneQueueId: network.outputs.privateDnsZoneQueueId
    privateDnsZoneTableId: network.outputs.privateDnsZoneTableId
    aiServicesResourceId: aiServicesResourceId
  }
}

module acr 'modules/acr.bicep' = {
  name: 'acr'
  scope: rg
  params: {
    projectName: projectName
    environment: environmentName
    location: location
    tags: tags
    subnetPrivateEndpointsId: network.outputs.subnetPrivateEndpointsId
    privateDnsZoneAcrId: network.outputs.privateDnsZoneAcrId
  }
}

module ai 'modules/ai.bicep' = {
  name: 'ai'
  scope: rg
  params: {
    projectName: projectName
    environment: environmentName
    location: location
    tags: tags
    subnetPrivateEndpointsId: network.outputs.subnetPrivateEndpointsId
    privateDnsZoneCognitiveId: network.outputs.privateDnsZoneCognitiveId
    privateDnsZoneOpenAIId: network.outputs.privateDnsZoneOpenAIId
    dataStorageAccountId: storage.outputs.dataStorageAccountId
  }
}

module monitoring 'modules/monitoring.bicep' = {
  name: 'monitoring'
  scope: rg
  params: {
    projectName: projectName
    environment: environmentName
    location: location
    tags: tags
    subnetPrivateEndpointsId: network.outputs.subnetPrivateEndpointsId
    privateDnsZoneMonitorId: network.outputs.privateDnsZoneMonitorId
    privateDnsZoneOmsId: network.outputs.privateDnsZoneOmsId
    privateDnsZoneOdsId: network.outputs.privateDnsZoneOdsId
    privateDnsZoneAgentsvcId: network.outputs.privateDnsZoneAgentsvcId
    privateDnsZoneBlobId: network.outputs.privateDnsZoneBlobId
  }
}

module functions 'modules/functions.bicep' = {
  name: 'functions'
  scope: rg
  params: {
    projectName: projectName
    environment: environmentName
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
    // 既存リソース名と一致させるため固定。新規環境では未指定にして uniqueString を使うか、
    // 既存 Function App 名のサフィックス（'func-transcription-{env}-XXXXXX' の XXXXXX 部分）を渡す。
    // 既存サイト func-transcription-dev-ddh4w2 が asp-transcription-dev に紐付いているため一致させる
    // （Flex Consumption は 1 プラン 1 サイトのみ）。
    functionAppNameSuffix: 'ddh4w2'
  }
}

module eventgrid 'modules/eventgrid.bicep' = {
  name: 'eventgrid'
  scope: rg
  params: {
    projectName: projectName
    environment: environmentName
    dataStorageAccountId: storage.outputs.dataStorageAccountId
  }
}

module containerApps 'modules/container-apps.bicep' = {  
  name: 'containerApps'  
  scope: rg  
  params: {  
    projectName: projectName  
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
  
    azureOpenAIEndpoint: ai.outputs.openAIEndpoint  
    azureOpenAIId: ai.outputs.openAIId  
    azureOpenAIChatDeployment: ai.outputs.chatDeployment  
    azureOpenAIEmbeddingDeployment: ai.outputs.embeddingDeployment  
    azureOpenAIApiVersion: '2024-10-21'  
  
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
    projectName: projectName  
    environment: environmentName  
    location: location  
    tags: tags  
    subnetPrivateEndpointsId: network.outputs.subnetPrivateEndpointsId  
    privateDnsZoneSearchId: network.outputs.privateDnsZoneSearchId  
  }  
}  

// ==============================================================================
// azd 用出力
// ==============================================================================
output AZURE_RESOURCE_GROUP string = rg.name
output AZURE_CONTAINER_REGISTRY_ENDPOINT string = acr.outputs.acrLoginServer
output AZURE_CONTAINER_REGISTRY_NAME string = acr.outputs.acrName
output SERVICE_FUNCTIONS_RESOURCE_NAME string = functions.outputs.functionAppName
output SERVICE_UI_RESOURCE_NAME string = containerApps.outputs.containerAppName
output AZURE_CONTAINER_APP_ENVIRONMENT_NAME string = containerApps.outputs.containerAppEnvironmentName
output AI_SERVICES_ENDPOINT string = ai.outputs.aiServicesEndpoint
output CONTAINER_APP_URL string = containerApps.outputs.containerAppUrl
output AZURE_OPENAI_ENDPOINT string = ai.outputs.openAIEndpoint  
output AZURE_OPENAI_CHAT_DEPLOYMENT string = ai.outputs.chatDeployment  
output AZURE_SEARCH_ENDPOINT string = search.outputs.searchEndpoint  
output AZURE_SEARCH_INDEX_NAME string = search.outputs.indexName  
