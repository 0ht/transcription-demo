// ==============================================================================
// Container Apps Environment + Container App (Streamlit UI)
// ==============================================================================
param projectName string
param environment string
param location string
param tags object
param subnetAcaId string
param logAnalyticsWorkspaceId string
param logAnalyticsWorkspaceResourceId string
param dataStorageAccountName string
param dataStorageAccountId string
param acrLoginServer string
param acrId string
param allowedIpRanges array

// Microsoft 公式 exists パターン:
// 既存 Container App が存在する場合は前回 image を引き継ぎ、registries 設定も投入する。
// 初回デプロイでは公開イメージのみで作成し AcrPull 依存と VNet 統合 ACA からの ACR 認証を回避。
@description('UI Container App が既存かどうか。azd が SERVICE_UI_RESOURCE_EXISTS から自動セット。')
param uiExists bool = false

// Container Apps Environment
// Container Apps Environment（ログは Diagnostic Settings でキーレス送信）
resource cae 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: 'cae-${projectName}-${environment}'
  location: location
  tags: tags
  properties: {
    vnetConfiguration: {
      infrastructureSubnetId: subnetAcaId
      internal: false
    }
    appLogsConfiguration: {
      destination: ''
    }
  }
}

// Diagnostic Settings: CAE → Log Analytics（キーレス・ARM レベルで接続）
resource caeDiagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'diag-cae-${environment}'
  scope: cae
  properties: {
    workspaceId: logAnalyticsWorkspaceResourceId
    logs: [
      { categoryGroup: 'allLogs', enabled: true }
    ]
  }
}

// Container App (Streamlit UI) — 初回は公開プレースホルダー、azd deploy で ACR イメージに更新
// 既存リソースがある場合は前回 image を引き継ぐ（exists パターン）
var containerAppName = 'ca-${projectName}-ui-${environment}'

module fetchLatestImage './fetch-container-image.bicep' = {
  name: 'fetch-ui-image'
  params: {
    exists: uiExists
    name: containerAppName
  }
}

resource containerApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: containerAppName
  location: location
  tags: union(tags, {
    'azd-service-name': 'ui'
  })
  identity: { type: 'SystemAssigned' }
  properties: {
    managedEnvironmentId: cae.id
    configuration: {
      ingress: {
        external: true
        targetPort: 8501
        transport: 'http'
        traffic: [
          {
            latestRevision: true
            weight: 100
          }
        ]
        ipSecurityRestrictions: [
          for (ip, i) in allowedIpRanges: {
            action: 'Allow'
            ipAddressRange: ip
            name: 'allow-internal-${i}'
          }
        ]
      }
      // registries 設定は azd の predeploy hook で `az containerapp registry set --identity system`
      // により CLI 経由で投入する（OCR-Demo パターン）。Bicep 側で常時登録すると、AcrPull
      // ロール割り当てとの並列デプロイで伝播待ちが発生し Container App の起動が
      // "Operation expired" でタイムアウトする。
      // - 初回 (uiExists=false): registries 空 + placeholder 公開イメージで起動 → predeploy が
      //   ACR public 化 + registry set → azd deploy が image 更新 → postdeploy で ACR 閉鎖
      // - 2回目以降 (uiExists=true): Bicep 側でも registries を冪等に登録し、再 provision でも
      //   設定がドリフトしない
      registries: uiExists ? [
        {
          server: acrLoginServer
          identity: 'system'
        }
      ] : []
    }
    template: {
      scale: {
        minReplicas: 0
        maxReplicas: 3
      }
      containers: [
        {
          name: 'streamlit-ui'
          // 既存があれば前回 image、なければ最軽量の公開プレースホルダー（auth 不要・サイズ小）
          image: !empty(fetchLatestImage.outputs.containers) ? fetchLatestImage.outputs.containers[0].image : 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: [
            { name: 'DATA_STORAGE_ACCOUNT_NAME', value: dataStorageAccountName }
            { name: 'CONTAINER_INPUT', value: 'input' }
            { name: 'CONTAINER_OUTPUT', value: 'output' }
            { name: 'CONTAINER_PROCESSED', value: 'processed' }
            { name: 'LOG_ANALYTICS_WORKSPACE_ID', value: logAnalyticsWorkspaceId }
          ]
        }
      ]
    }
  }
}

// RBAC: Container App → データ Storage (読み取り + input への書き込み)
resource dataStorage 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: dataStorageAccountName
}

resource acaBlobContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(dataStorageAccountId, containerApp.id, 'Storage Blob Data Contributor')
  scope: dataStorage
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
    principalId: containerApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource acaQueueContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(dataStorageAccountId, containerApp.id, 'Storage Queue Data Contributor')
  scope: dataStorage
  properties: {
    // Storage Queue Data Contributor (poison キューの clear/delete に必要)
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '974c5e8b-45b9-4653-ba55-5f855dd0fb88')
    principalId: containerApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// RBAC: Container App → Log Analytics (ログ読み取り)
resource logAnalyticsWorkspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' existing = {
  name: last(split(logAnalyticsWorkspaceResourceId, '/'))
}

resource acaLogReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(logAnalyticsWorkspaceResourceId, containerApp.id, 'Log Analytics Reader')
  scope: logAnalyticsWorkspace
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '73c42c96-874c-492b-b04d-ab87d138a893')
    principalId: containerApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// RBAC: Container App → ACR (AcrPull)
resource acrResource 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' existing = {
  name: last(split(acrId, '/'))
}

resource acaAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acrId, containerApp.id, 'AcrPull')
  scope: acrResource
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')
    principalId: containerApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

output containerAppName string = containerApp.name
output containerAppEnvironmentName string = cae.name
output containerAppUrl string = 'https://${containerApp.properties.configuration.ingress.fqdn}'
