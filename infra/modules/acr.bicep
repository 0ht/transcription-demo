// ==============================================================================
// Azure Container Registry (Premium + Private Endpoint)
// ==============================================================================
param projectName string
param environment string
param location string
param tags object
param subnetPrivateEndpointsId string
param privateDnsZoneAcrId string

resource acr 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' = {
  name: 'acr${replace(projectName, '-', '')}${environment}'
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
  name: 'pe-acr-${environment}'
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
