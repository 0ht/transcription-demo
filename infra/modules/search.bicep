@description('リソースのデプロイ先リージョン。')  
param location string  
@description('リソースに付与する共通タグ。')  
param tags object  
@description('Private Endpoint を配置するサブネットのリソース ID。')  
param subnetPrivateEndpointsId string  
@description('Search 用 Private DNS ゾーンのリソース ID。')  
param privateDnsZoneSearchId string  
  
@description('Search service sku')  
param sku string = 'basic'  
  
@description('Search index name')  
param indexName string = 'documents'  
  
@description('Semantic configuration name')  
param semanticConfigName string = 'default-semantic'  
  
@description('Search サービス名（トークン付与済み最終名）')  
param searchName string  
  
@description('Search の Private Endpoint 名')  
param peSearchName string  
  
resource search 'Microsoft.Search/searchServices@2023-11-01' = {  
  name: searchName  
  location: location  
  tags: tags  
  sku: {  
    name: sku  
  }  
  identity: {  
    type: 'SystemAssigned'  
  }  
  properties: {  
    publicNetworkAccess: 'disabled'  
    disableLocalAuth: true  
    replicaCount: 1  
    partitionCount: 1  
    hostingMode: 'default'  
  }  
}  
  
resource peSearch 'Microsoft.Network/privateEndpoints@2024-01-01' = {  
  name: peSearchName  
  location: location  
  tags: tags  
  properties: {  
    subnet: {  
      id: subnetPrivateEndpointsId  
    }  
    privateLinkServiceConnections: [  
      {  
        name: 'psc-search'  
        properties: {  
          privateLinkServiceId: search.id  
          groupIds: [  
            'searchService'  
          ]  
        }  
      }  
    ]  
  }  
}  
  
resource peSearchDns 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-01-01' = {  
  parent: peSearch  
  name: 'dns-zone-group-search'  
  properties: {  
    privateDnsZoneConfigs: [  
      {  
        name: 'search'  
        properties: {  
          privateDnsZoneId: privateDnsZoneSearchId  
        }  
      }  
    ]  
  }  
}  
  
output searchServiceName string = search.name  
output searchEndpoint string = 'https://${search.name}.search.windows.net'  
output searchId string = search.id  
output searchPrincipalId string = search.identity.principalId  
output indexName string = indexName  
output semanticConfigName string = semanticConfigName  
