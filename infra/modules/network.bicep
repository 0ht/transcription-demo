// ==============================================================================
// VNet + Subnets + Private DNS Zones
// ==============================================================================
param projectName string
param environment string
param location string
param tags object

var vnetName = 'vnet-${projectName}-${environment}'

resource vnet 'Microsoft.Network/virtualNetworks@2024-01-01' = {
  name: vnetName
  location: location
  tags: tags
  properties: {
    addressSpace: {
      addressPrefixes: ['10.0.0.0/16']
    }
    subnets: [
      {
        name: 'snet-functions'
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
      {
        name: 'snet-aca'
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
      {
        name: 'snet-privateendpoints'
        properties: {
          addressPrefix: '10.0.4.0/24'
          defaultOutboundAccess: false
        }
      }
    ]
  }
}

// Private DNS Zones
var dnsZones = {
  blob: 'privatelink.blob.core.windows.net'
  queue: 'privatelink.queue.core.windows.net'
  table: 'privatelink.table.core.windows.net'
  functions: 'privatelink.azurewebsites.net'
  cognitive: 'privatelink.cognitiveservices.azure.com'
  acr: 'privatelink.azurecr.io'
  monitor: 'privatelink.monitor.azure.com'
  oms: 'privatelink.oms.opinsights.azure.com'
  ods: 'privatelink.ods.opinsights.azure.com'
  agentsvc: 'privatelink.agentsvc.azure-automation.net'
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
output subnetFunctionsId string = vnet.properties.subnets[0].id
output subnetAcaId string = vnet.properties.subnets[1].id
output subnetPrivateEndpointsId string = vnet.properties.subnets[2].id

// DNS Zone IDs - dnsZones 辞書のキー名で indexOf 参照。
// 新規ゾーンの追加によりソート順がずれても名前で解決されるため安全。
var dnsZoneKeys = objectKeys(dnsZones)
output privateDnsZoneBlobId string = privateDnsZones[indexOf(dnsZoneKeys, 'blob')].id
output privateDnsZoneQueueId string = privateDnsZones[indexOf(dnsZoneKeys, 'queue')].id
output privateDnsZoneTableId string = privateDnsZones[indexOf(dnsZoneKeys, 'table')].id
output privateDnsZoneFunctionsId string = privateDnsZones[indexOf(dnsZoneKeys, 'functions')].id
output privateDnsZoneCognitiveId string = privateDnsZones[indexOf(dnsZoneKeys, 'cognitive')].id
output privateDnsZoneAcrId string = privateDnsZones[indexOf(dnsZoneKeys, 'acr')].id
output privateDnsZoneMonitorId string = privateDnsZones[indexOf(dnsZoneKeys, 'monitor')].id
output privateDnsZoneOmsId string = privateDnsZones[indexOf(dnsZoneKeys, 'oms')].id
output privateDnsZoneOdsId string = privateDnsZones[indexOf(dnsZoneKeys, 'ods')].id
output privateDnsZoneAgentsvcId string = privateDnsZones[indexOf(dnsZoneKeys, 'agentsvc')].id
