// ==============================================================================
// Monitoring: Log Analytics + App Insights + AMPLS + Private Endpoint
// ==============================================================================
@description('リソースのデプロイ先リージョン。')
param location string
@description('リソースに付与する共通タグ。')
param tags object
@description('Private Endpoint を配置するサブネットのリソース ID。')
param subnetPrivateEndpointsId string
@description('Monitor 用 Private DNS ゾーンのリソース ID。')
param privateDnsZoneMonitorId string
@description('OMS 用 Private DNS ゾーンのリソース ID。')
param privateDnsZoneOmsId string
@description('ODS 用 Private DNS ゾーンのリソース ID。')
param privateDnsZoneOdsId string
@description('Agentsvc 用 Private DNS ゾーンのリソース ID。')
param privateDnsZoneAgentsvcId string
@description('Blob 用 Private DNS ゾーンのリソース ID。')
param privateDnsZoneBlobId string

@description('Log Analytics ワークスペース名')
param logAnalyticsName string

@description('Application Insights 名')
param appInsightsName string

@description('Azure Monitor Private Link Scope 名')
param amplsName string

@description('Monitor の Private Endpoint 名')
param peMonitorName string

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: logAnalyticsName
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: appInsightsName
  location: location
  tags: tags
  kind: 'other'
  properties: {
    Application_Type: 'other'
    WorkspaceResourceId: logAnalytics.id
  }
}

resource ampls 'Microsoft.Insights/privateLinkScopes@2021-07-01-preview' = {
  name: amplsName
  location: 'global'
  tags: tags
  properties: {
    accessModeSettings: {
      // ingestion: PrivateOnly
      //   Functions / Container Apps は VNet 統合済みのため Private Endpoint 経由で送信可能。
      //   VNet 外（オンプレ等）からの Application Insights SDK 経由テレメトリは遮断される。
      // query    : Open
      //   PoC リポジトリのため、Azure Portal の Logs ブレード / ローカル PC からの
      //   `az monitor app-insights query` 等で運用調査ができるように Open のままにしている。
      //   本番展開時は PrivateOnly に切り替え、Bastion / Jumpbox 等の VNet 内クライアントから
      //   クエリする運用に変更すること（docs/deploy-guide.md §15.2 参照）。
      ingestionAccessMode: 'PrivateOnly'
      queryAccessMode: 'Open'
    }
  }
}

resource amplsLogAnalytics 'Microsoft.Insights/privateLinkScopes/scopedResources@2021-07-01-preview' = {
  parent: ampls
  name: 'amplss-log'
  properties: {
    linkedResourceId: logAnalytics.id
  }
}

resource amplsAppInsights 'Microsoft.Insights/privateLinkScopes/scopedResources@2021-07-01-preview' = {
  parent: ampls
  name: 'amplss-appi'
  properties: {
    linkedResourceId: appInsights.id
  }
}

resource peMonitor 'Microsoft.Network/privateEndpoints@2024-01-01' = {
  name: peMonitorName
  location: location
  tags: tags
  properties: {
    subnet: { id: subnetPrivateEndpointsId }
    privateLinkServiceConnections: [
      {
        name: 'psc-monitor'
        properties: {
          privateLinkServiceId: ampls.id
          groupIds: ['azuremonitor']
        }
      }
    ]
  }
  dependsOn: [
    amplsLogAnalytics
    amplsAppInsights
  ]
}

resource peMonitorDns 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-01-01' = {
  parent: peMonitor
  name: 'dns-zone-group-monitor'
  properties: {
    privateDnsZoneConfigs: [
      { name: 'monitor', properties: { privateDnsZoneId: privateDnsZoneMonitorId } }
      { name: 'oms', properties: { privateDnsZoneId: privateDnsZoneOmsId } }
      { name: 'ods', properties: { privateDnsZoneId: privateDnsZoneOdsId } }
      { name: 'agentsvc', properties: { privateDnsZoneId: privateDnsZoneAgentsvcId } }
      { name: 'blob', properties: { privateDnsZoneId: privateDnsZoneBlobId } }
    ]
  }
}

output logAnalyticsWorkspaceId string = logAnalytics.properties.customerId
output logAnalyticsWorkspaceResourceId string = logAnalytics.id
output logAnalyticsWorkspaceName string = logAnalytics.name
output applicationInsightsConnectionString string = appInsights.properties.ConnectionString
output applicationInsightsName string = appInsights.name
output applicationInsightsId string = appInsights.id
