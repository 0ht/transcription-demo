// ==============================================================================
// VNet + Subnets + Private DNS Zones
// ==============================================================================
@description('リソースのデプロイ先リージョン。')
param location string
@description('リソースに付与する共通タグ。')
param tags object

@description('VNet 名')
param vnetName string

@description('Functions サブネット名')
param subnetFunctionsName string

@description('ACA サブネット名')
param subnetAcaName string

@description('Private Endpoint サブネット名')
param subnetPrivateEndpointsName string

@description('Foundry Agent Service のネットワークインジェクション用サブネット名')
param subnetAgentName string

resource vnet 'Microsoft.Network/virtualNetworks@2024-01-01' = {
  name: vnetName
  location: location
  tags: tags
  properties: {
    addressSpace: {
      addressPrefixes: ['10.0.0.0/16']
    }
  }
}

resource subnetFunctions 'Microsoft.Network/virtualNetworks/subnets@2024-01-01' = {
  parent: vnet
  name: subnetFunctionsName
  properties: {
    addressPrefix: '10.0.1.0/24'
    defaultOutboundAccess: false
    delegations: [
      {
        name: 'functions-delegation'
        properties: {
          serviceName: 'Microsoft.App/environments'
        }
      }
    ]
  }
}

resource subnetAca 'Microsoft.Network/virtualNetworks/subnets@2024-01-01' = {
  parent: vnet
  name: subnetAcaName
  dependsOn: [
    subnetFunctions
  ]
  properties: {
    addressPrefix: '10.0.2.0/23'
    // Container Apps が初回起動の placeholder image (mcr.microsoft.com) や
    // ACR からのイメージ pull を行うため outbound 許可。
    // 本番では NAT Gateway + Egress リストで送信先を絞ることを推奨。
    defaultOutboundAccess: true
    delegations: [
      {
        name: 'aca-delegation'
        properties: {
          serviceName: 'Microsoft.App/environments'
        }
      }
    ]
  }
}

resource subnetPrivateEndpoints 'Microsoft.Network/virtualNetworks/subnets@2024-01-01' = {
  parent: vnet
  name: subnetPrivateEndpointsName
  dependsOn: [
    subnetAca
  ]
  properties: {
    addressPrefix: '10.0.4.0/24'
    defaultOutboundAccess: false
    privateEndpointNetworkPolicies: 'Disabled'
  }
}

// Foundry Agent Service の送信トラフィックを VNet に注入する専用サブネット。
// 要件: Microsoft.App/environments へ委任し、/27 以上のサイズであること。
// Agent 専用のため他リソース（Functions / ACA）とは共有しない。
resource subnetAgent 'Microsoft.Network/virtualNetworks/subnets@2024-01-01' = {
  parent: vnet
  name: subnetAgentName
  dependsOn: [
    subnetPrivateEndpoints
  ]
  properties: {
    addressPrefix: '10.0.5.0/24'
    defaultOutboundAccess: false
    delegations: [
      {
        name: 'agent-delegation'
        properties: {
          serviceName: 'Microsoft.App/environments'
        }
      }
    ]
  }
}

// Private DNS Zones
var dnsZones = {
  blob: 'privatelink.blob.${az.environment().suffixes.storage}'
  queue: 'privatelink.queue.${az.environment().suffixes.storage}'
  table: 'privatelink.table.${az.environment().suffixes.storage}'
  functions: 'privatelink.azurewebsites.net'
  cognitive: 'privatelink.cognitiveservices.azure.com'
  openai: 'privatelink.openai.azure.com'
  servicesai: 'privatelink.services.ai.azure.com'
  acr: 'privatelink.azurecr.io'
  monitor: 'privatelink.monitor.azure.com'
  oms: 'privatelink.oms.opinsights.azure.com'
  ods: 'privatelink.ods.opinsights.azure.com'
  agentsvc: 'privatelink.agentsvc.azure-automation.net'
  search: 'privatelink.search.windows.net'
  cosmos: 'privatelink.documents.azure.com'
}

resource privateDnsZones 'Microsoft.Network/privateDnsZones@2024-06-01' = [
  for (zone, _) in objectKeys(dnsZones): {
    name: dnsZones[zone]
    location: 'global'
    tags: tags
  }
]

resource vnetLinks 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = [
  for (zone, i) in objectKeys(dnsZones): {
    parent: privateDnsZones[i]
    name: 'vnetlink-${zone}'
    location: 'global'
    tags: tags
    properties: {
      virtualNetwork: {
        id: vnet.id
      }
      registrationEnabled: false
    }
  }
]

// Outputs
output vnetId string = vnet.id
output subnetFunctionsId string = subnetFunctions.id
output subnetAcaId string = subnetAca.id
output subnetPrivateEndpointsId string = subnetPrivateEndpoints.id
output subnetAgentId string = subnetAgent.id

// DNS Zone IDs - dnsZones 辞書のキー名で indexOf 参照。
// 新規ゾーンの追加によりソート順がずれても名前で解決されるため安全。
var dnsZoneKeys = objectKeys(dnsZones)
output privateDnsZoneBlobId string = privateDnsZones[indexOf(dnsZoneKeys, 'blob')].id
output privateDnsZoneQueueId string = privateDnsZones[indexOf(dnsZoneKeys, 'queue')].id
output privateDnsZoneTableId string = privateDnsZones[indexOf(dnsZoneKeys, 'table')].id
output privateDnsZoneFunctionsId string = privateDnsZones[indexOf(dnsZoneKeys, 'functions')].id
output privateDnsZoneCognitiveId string = privateDnsZones[indexOf(dnsZoneKeys, 'cognitive')].id
output privateDnsZoneOpenAIId string = privateDnsZones[indexOf(dnsZoneKeys, 'openai')].id
output privateDnsZoneServicesAiId string = privateDnsZones[indexOf(dnsZoneKeys, 'servicesai')].id
output privateDnsZoneAcrId string = privateDnsZones[indexOf(dnsZoneKeys, 'acr')].id
output privateDnsZoneMonitorId string = privateDnsZones[indexOf(dnsZoneKeys, 'monitor')].id
output privateDnsZoneOmsId string = privateDnsZones[indexOf(dnsZoneKeys, 'oms')].id
output privateDnsZoneOdsId string = privateDnsZones[indexOf(dnsZoneKeys, 'ods')].id
output privateDnsZoneAgentsvcId string = privateDnsZones[indexOf(dnsZoneKeys, 'agentsvc')].id
output privateDnsZoneSearchId string = privateDnsZones[indexOf(dnsZoneKeys, 'search')].id
output privateDnsZoneCosmosId string = privateDnsZones[indexOf(dnsZoneKeys, 'cosmos')].id
