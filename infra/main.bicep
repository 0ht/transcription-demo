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
@description('azd 実行者の Entra ID オブジェクト ID。空の場合はデプロイ者に RBAC を付与しない。')
param principalId string = ''

@description('principalId の種別。デフォルト User、CI では ServicePrincipal。')
@allowed([
  'User'
  'ServicePrincipal'
  'Group'
])
param principalType string = 'User'

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
    // サフィックスは functions.bicep 側で uniqueString(resourceGroup().id) から生成されるため、
    // 同一 RG で再 provision しても Function App 名は一意に保たれる。
    // 特定の既存名に合わせたい場合のみ functionAppNameSuffix を明示的に上書きする。
  }
}

module eventgrid 'modules/eventgrid.bicep' = {
  name: 'eventgrid'
  scope: rg
  params: {
    projectName: projectName
    environment: environmentName
    dataStorageAccountId: storage.outputs.dataStorageAccountId
    dataStorageAccountName: storage.outputs.dataStorageAccountName
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
  }
}

// ==============================================================================
// Deployer RBAC — azd 実行者本人にも開発・運用用ロールを付与
// principalId が空のとき（CI で未注入など）はスキップ
// ==============================================================================
module deployerRbac 'modules/deployer-rbac.bicep' = if (!empty(principalId)) {
  name: 'deployerRbac'
  scope: rg
  params: {
    principalId: principalId
    principalType: principalType
    dataStorageAccountName: storage.outputs.dataStorageAccountName
    acrName: acr.outputs.acrName
    logAnalyticsWorkspaceName: monitoring.outputs.logAnalyticsWorkspaceName
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
output AZURE_DATA_STORAGE_ACCOUNT_NAME string = storage.outputs.dataStorageAccountName
