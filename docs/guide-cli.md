# Test it from the command line

Calls SparkX 2.6 with PowerShell only, no admin page. Port 29203. Replace `<...>` values. The commands were written from the code and not yet run against a live server.

## Get a token

Every call except `/health` needs a token while `SERVICE_ADMIN_PASSWORD` is set. Admin calls take the admin password itself; a token works only on `/v1/*`. Create one token and reuse it. Each `POST /admin/tokens` call makes a new one, so do not put it in a loop. Revoke extras on the admin page.

```powershell
$base = "http://127.0.0.1:29203"
$pw   = @{ "X-Internal-Token" = "<SERVICE_ADMIN_PASSWORD>" }
(Invoke-RestMethod -Method Post -Uri "$base/admin/tokens" -Headers $pw -ContentType "application/json" -Body '{"name":"cli-test"}').token
```

The token is shown once. Put it in `$h`. With no admin password set there is no auth: skip `-Headers $h`.

```powershell
$h = @{ "X-Internal-Token" = "<token from above>" }
Invoke-RestMethod "$base/health"
```

## Call it

```powershell
$req = @{ messages = @(@{ role = "user"; content = "Say hello in five words." }); max_tokens = 64 } | ConvertTo-Json -Depth 5
Invoke-RestMethod -Method Post -Uri "$base/v1/chat" -Headers $h -ContentType "application/json; charset=utf-8" -Body ([Text.Encoding]::UTF8.GetBytes($req))
```

Add `stream = $true` to get JSON lines while the answer is made.

Tools, schema and thinking get 422.

## Problems

| Answer | Cause |
|---|---|
| `401` | Wrong value: admin routes need the admin password, `/v1/*` needs a token. |
| `403` | The token's *projects* list does not include what you asked for. |
| `429` | Token rate used up or too many calls waiting; wait `Retry-After` seconds. |
| `422` | A field is unknown or out of range; the answer says which. |
| connection refused | Not running, or another port; see the run-path guides. |
