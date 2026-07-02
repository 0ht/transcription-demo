// ==============================================================================
// Azure Container Registry (Premium + Private Endpoint)
// ==============================================================================
@description('リソースのデプロイ先リージョン。')
param location string
@description('リソースに付与する共通タグ。')
param tags object
@description('Private Endpoint を配置するサブネットのリソース ID。')
param subnetPrivateEndpointsId string
@description('ACR 用 Private DNS ゾーンのリソース ID。')
param privateDnsZoneAcrId string

@description('ACR 名（トークン付与済み最終名）')
param acrName string

@description('ACR の Private Endpoint 名')
param peAcrName string

resource acr 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' = {
  #disable-next-line BCP334
  name: acrName
  location: location
  tags: tags
  sku: { name: 'Premium' }
  properties: {
    adminUserEnabled: false
    publicNetworkAccess: 'Disabled'
    networkRuleSet: {
      defaultAction: 'Deny'
    }
  }
}

resource peAcr 'Microsoft.Network/privateEndpoints@2024-01-01' = {
  name: peAcrName
  location: location
  tags: tags
  properties: {
    subnet: { id: subnetPrivateEndpointsId }
    privateLinkServiceConnections: [
      {
        name: 'psc-acr'
        properties: {
          privateLinkServiceId: acr.id
          groupIds: ['registry']
        }
      }
    ]
  }
}

resource peAcrDns 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-01-01' = {
  parent: peAcr
  name: 'dns-zone-group-acr'
  properties: {
    privateDnsZoneConfigs: [
      { name: 'acr', properties: { privateDnsZoneId: privateDnsZoneAcrId } }
    ]
  }
}

output acrName string = acr.name
output acrLoginServer string = acr.properties.loginServer
output acrId string = acr.id
