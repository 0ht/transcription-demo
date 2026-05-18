// ==============================================================================
// Deployer RBAC — azd 実行者（principalId）に開発・運用に必要なロールを付与
// ==============================================================================
@description('azd 実行者の Entra ID オブジェクト ID')
param principalId string

@description('プリンシパル種別。ユーザーは User、CI のサービスプリンシパルは ServicePrincipal。')
@allowed([
  'User'
  'ServicePrincipal'
  'Group'
])
param principalType string = 'User'

@description('データ用 Storage Account 名')
param dataStorageAccountName string

@description('ACR 名')
param acrName string

@description('Log Analytics ワークスペース名')
param logAnalyticsWorkspaceName string

// 既存リソース参照（同一 RG 内）
resource dataStorage 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: dataStorageAccountName
}

resource acr 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' existing = {
  name: acrName
}

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' existing = {
  name: logAnalyticsWorkspaceName
}

// Storage Blob Data Contributor — Blob のアップロード/ダウンロード（input/output/processed 操作）
resource deployerBlobContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(dataStorage.id, principalId, 'Storage Blob Data Contributor')
  scope: dataStorage
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
    principalId: principalId
    principalType: principalType
  }
}

// Storage Queue Data Contributor — poison キュー確認・クリア
resource deployerQueueContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(dataStorage.id, principalId, 'Storage Queue Data Contributor')
  scope: dataStorage
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '974c5e8b-45b9-4653-ba55-5f855dd0fb88')
    principalId: principalId
    principalType: principalType
  }
}

// AcrPush — ローカルから ACR への手動 push（デバッグ・検証用）
resource deployerAcrPush 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acr.id, principalId, 'AcrPush')
  scope: acr
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '8311e382-0749-4cb8-b61a-304f252e45ec')
    principalId: principalId
    principalType: principalType
  }
}

// Log Analytics Reader — KQL クエリでログ調査
resource deployerLogReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(logAnalytics.id, principalId, 'Log Analytics Reader')
  scope: logAnalytics
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '73c42c96-874c-492b-b04d-ab87d138a893')
    principalId: principalId
    principalType: principalType
  }
}
