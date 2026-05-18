// ==============================================================================
// Monitoring: Log Analytics + App Insights + AMPLS + Private Endpoint
// ==============================================================================
param projectName string
param environment string
param location string
param tags object
param subnetPrivateEndpointsId string
param privateDnsZoneMonitorId string
param privateDnsZoneOmsId string
param privateDnsZoneOdsId string
param privateDnsZoneAgentsvcId string
param privateDnsZoneBlobId string

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'log-${projectName}-${environment}'
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: 'appi-${projectName}-${environment}'
  location: location
  tags: tags
  kind: 'other'
  properties: {
    Application_Type: 'other'
    WorkspaceResourceId: logAnalytics.id
  }
}

resource ampls 'Microsoft.Insights/privateLinkScopes@2021-07-01-preview' = {
  name: 'ampls-${projectName}-${environment}'
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
  name: 'pe-monitor-${environment}'
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
