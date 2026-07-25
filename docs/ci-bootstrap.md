# Factory CI bootstrap

This is the 15-minute, opt-in path for adding the Wolftac Factory checks to another
GitHub repository without breaking the factory's direct fast-forward fallback.

## 1. Add the workflow

Create `.github/workflows/factory-checks.yml`:

```yaml
name: Factory Checks

on:
  pull_request:
    branches: [main]

jobs:
  factory-checks:
    name: factory-checks
    runs-on: ubuntu-latest
    defaults:
      run:
        shell: pwsh
    steps:
      - name: Check out repository
        uses: actions/checkout@v4

      - name: Check cross-port parity
        run: ./deploy/parity-check.ps1

      - name: Check workflow semantics
        run: ./deploy/semantic-check.ps1

      - name: Reject CR bytes in tracked source documents
        run: |
          $extensions = @(".md", ".json", ".ps1", ".yml")
          $tracked = @(git ls-files | Where-Object {
            $extensions -contains [IO.Path]::GetExtension($_).ToLowerInvariant()
          })
          $withCr = @($tracked | Where-Object {
            ([IO.File]::ReadAllBytes($_) -contains 13)
          })
          if ($withCr.Count -gt 0) {
            $withCr | ForEach-Object { Write-Output "CR BYTE: $_" }
            throw "LF check failed: tracked source documents contain CR bytes"
          }
          Write-Output "LF OK: $($tracked.Count) tracked source documents contain no CR bytes"
```

The parity and semantic scripts run their complete repo-side assertions in CI. They
do not compare user-profile deployment targets, so no deployed-file path is skipped.

Open a pull request and wait for the stable `factory-checks` job to pass before
protecting `main`.

## 2. Require only the factory check

Prerequisite: the repository's GitHub plan must support branch protection. For a
private repository, an HTTP 403 response saying `Upgrade to GitHub Pro or make this
repository public to enable this feature` means the PUT made no change. Stop there;
do not claim the check is required and do not make the repository public as a
workaround without explicit owner approval.

Replace the owner and repository in the endpoint when bootstrapping another repo.
This is the exact PowerShell command used for
`WolftacDigital/wolftac-factory`:

```powershell
@'
{
  "required_status_checks": {
    "strict": false,
    "contexts": ["factory-checks"]
  },
  "enforce_admins": false,
  "required_pull_request_reviews": null,
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false
}
'@ | gh api -X PUT repos/WolftacDigital/wolftac-factory/branches/main/protection --input -
```

`enforce_admins:false` is required. The factory's transition path for older,
pre-PR issues is an admin-authenticated fast-forward push to `main`; enforcing the
rule for admins would block that path and could strand in-flight issues. Do not add
required PR reviews or push restrictions.

Read the effective protection back:

```powershell
gh api repos/WolftacDigital/wolftac-factory/branches/main/protection
```

Then exercise the transition path with a safe no-op push of the current `main` SHA:

```powershell
git push origin <CURRENT_MAIN_SHA>:main
```

If that push is rejected, immediately run the rollback command below. Never leave a
protection rule enabled when the factory cannot exercise its merge path.

## Rollback

1. Revert the commit that introduced the PR/CI skill changes.
2. Delete `.github/workflows/factory-checks.yml` in that revert.
3. Remove branch protection with the exact command:

```powershell
gh api -X DELETE repos/WolftacDigital/wolftac-factory/branches/main/protection
```

Read the branch endpoint afterward and confirm protection is absent.

## Jonathan-only emergency override

Only Jonathan may authorize an emergency override. Temporarily lift protection with
the DELETE command above, perform the narrowly approved recovery, and immediately
restore the exact safe payload and re-run the read-back plus no-op push proof. An
emergency override never changes the factory's human merge-approval requirement.
