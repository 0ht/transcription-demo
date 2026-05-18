$rg = "rg-transcription-dev"
$newPid = az containerapp show -n ca-transcription-ui-dev -g $rg --query identity.principalId -o tsv
Write-Host "New CA principalId: $newPid"

$scopes = @(
    (az storage account show -n sttranscriptiondatadev -g $rg --query id -o tsv),
    (az acr show -n acrtranscriptiondev --query id -o tsv),
    (az monitor log-analytics workspace show -n log-transcription-dev -g $rg --query id -o tsv)
)

$targetRoles = @(
    "Storage Blob Data Contributor",
    "Storage Queue Data Contributor",
    "AcrPull",
    "Log Analytics Reader"
)

foreach ($scope in $scopes) {
    Write-Host "--- Scanning $($scope.Split('/')[-1]) ---"
    $all = az role assignment list --scope $scope -o json | ConvertFrom-Json
    foreach ($ra in $all) {
        if ($targetRoles -contains $ra.roleDefinitionName -and $ra.principalId -ne $newPid) {
            Write-Host "Deleting RA name=$($ra.name) principalId=$($ra.principalId) role=$($ra.roleDefinitionName)"
            az role assignment delete --ids $ra.id --yes | Out-Null
        }
    }
}
Write-Host "Done."
