# ryanruddock.com

A personal site, styled like the web circa 1999.

## Layout

| Path | What it is | Deployed? |
|---|---|---|
| `index.html` | the site | yes |
| `nexus.html` | Project Nexus walkthrough (static, invented data) | yes |
| `roadmap.html` | emulator learning roadmap | yes |
| `diagrams/`, `thumbs/`, `fonts/`, `dog.png` | page assets | yes |
| `infra/` | Terraform for the guestbook + visitor counter | no |
| `local/` | local dev backend | no |
| `CONTEXT.md` | operational notes, gotchas, recovery steps | no |

`.github/workflows/deploy.yml` syncs the repo root to S3 and invalidates
CloudFront on every push to `main`. `infra/`, `local/`, `*.md` and `.gitignore`
are excluded — without those excludes, Terraform state and the dev server would
be published on the public site.

Read `CONTEXT.md` before changing anything structural.

## Running it locally

```sh
python3 local/server.py     # http://127.0.0.1:8099/
```

Serves the site and answers the same three API routes the deployed function
does, with identical validation. State is JSON in `local/` and is gitignored.

## The backend

The counter and guestbook are a single Lambda behind a function URL, with one
DynamoDB table. No API Gateway: a function URL is a first-class HTTPS endpoint
at no charge beyond the invocation, which removes the per-request cost from a
workload that is otherwise inside the free tier.

`index.html` picks its API base by hostname — empty on localhost so
`local/server.py` answers, the function URL anywhere else. If neither responds,
the guestbook and counter modules hide themselves rather than showing an error.

### Cost guardrails

Three layers, fastest first:

1. **Account concurrency limit (10)** — the outer ceiling. AWS rejects any
   per-function reservation that would drop unreserved concurrency below 10,
   so on this account `max_concurrency` stays at -1 and the account limit does
   the capping. Raise the quota to 1000 and a real per-function cap becomes
   possible.
2. **Invocation alarm → killswitch** — a CloudWatch alarm on invocation volume
   publishes to SNS, and a killswitch Lambda sets reserved concurrency to 0.
   Volume is visible within minutes; cost is not.
3. **Budget (`budget_amount`, default $5)** — a backstop. AWS Budgets refreshes
   at most three times a day, so it is a detective control, not a preventive
   one. That is why it is third rather than first.

Realistic cost is $0 — the free tier covers it. See `terraform output` for the
re-arm command after a trip.

### Applying

State lives in `s3://ryanruddock-com-tfstate` (private, versioned, encrypted),
configured in `infra/backend.tf`. The bucket is bootstrapped outside Terraform —
see `CONTEXT.md`.

```sh
cd infra
terraform init
terraform apply -var 'alert_email=you@example.com'
```

Then copy the `api_base_url` output into the `API_BASE_URL` constant in
`index.html` and push.

Apply with your own credentials, not the CI deploy key — this stack creates IAM
roles, and a key that lives in GitHub Actions should not be able to mint those.
