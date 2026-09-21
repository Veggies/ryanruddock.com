# Context

Operational notes for picking this back up. `README.md` covers what the project
*is*; this covers what bit us and what you need to know before touching it.

**This repo is public.** Nothing here names the account ID, key IDs, or anything
else worth enumerating. Keep it that way — run `aws sts get-caller-identity` if
you need the account number.

## The moving parts

| Thing | Where |
|---|---|
| Site content | `index.html`, `dog.png` at the repo root |
| Guestbook + counter infra | `infra/` (Terraform) |
| Local dev backend | `local/server.py` |
| Terraform state | `s3://ryanruddock-com-tfstate/ryanruddock.com/guestbook.tfstate` |

### Buckets, and which one matters

- **`www.ryanruddock.com`** — the bucket that actually serves the site. This is
  what `S3_BUCKET` in the repo secrets points at.
- **`ryanruddock.com`** — apex. Redirects to `www`, and holds one stray
  `index.htm` (note: `.htm`) that nothing appears to use. Left alone.
- **`ryanruddock-com-tfstate`** — Terraform state. Private, versioned,
  encrypted, noncurrent versions expire after 90 days.
- `cloudtrail-ryanruddock-com`, `cf-templates-…` — predate this work, untouched.

The live site is at **`https://www.ryanruddock.com`**; the apex 301s to it. Any
CORS origin list has to include both, which `infra/variables.tf` does.

## Traps worth remembering

**The deploy workflow publishes the whole repo.** `aws s3 sync .` uploads
everything except what is explicitly excluded, so a new top-level directory is
public the moment you push. `infra/`, `local/`, `*.md` and `.gitignore` are
excluded — without those, Terraform state would be served from the website.
**Add an exclude before adding a directory**, not after.

**State bucket is not managed by Terraform.** A backend cannot create the bucket
holding its own state. It was bootstrapped with the CLI. If it is ever lost, the
recovery is to recreate it and `terraform import` all 17 resources — so do not
delete it casually. To rebuild it:

```sh
B=ryanruddock-com-tfstate
aws s3api create-bucket --bucket $B --region us-east-1
aws s3api put-public-access-block --bucket $B --public-access-block-configuration \
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
aws s3api put-bucket-versioning --bucket $B --versioning-configuration Status=Enabled
aws s3api put-bucket-encryption --bucket $B --server-side-encryption-configuration \
  '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"},"BucketKeyEnabled":true}]}'
```

**Lambda concurrency is capped at 10 on this account.** That is the default for
an account that has never requested an increase, and AWS rejects any
per-function reservation that would push unreserved concurrency below 10. So
`max_concurrency` is `-1` (no reservation) and the account limit does the
capping. If you raise the quota to 1000 in Service Quotas, set
`max_concurrency = 2` and you get a real per-function ceiling.

**Guestbook sort keys need sub-second precision.** They lead with a timestamp;
at second precision two posts in the same second ordered by their random UUID
suffix instead of by time. They use microseconds now. Do not "simplify" that
back.

**Apply with your own credentials, not the CI deploy key.** This stack creates
IAM roles. The key in GitHub Actions should stay scoped to S3 + CloudFront.

## Routine tasks

```sh
# local dev (site + working guestbook, state as JSON in local/)
python3 local/server.py                      # http://127.0.0.1:8099/

# infra
cd infra
terraform init                               # backend config is in backend.tf
terraform plan  -var 'alert_email=you@example.com'
terraform apply -var 'alert_email=you@example.com'
```

Changing `index.html` needs no Terraform — push to `main` and the workflow
syncs and invalidates CloudFront.

### If the killswitch trips

Symptom: the guestbook and visitor counter vanish from the page (they hide
themselves when the API does not answer) while the rest of the site is fine.

```sh
aws cloudwatch describe-alarms --alarm-names ryanruddock-guestbook-invocation-flood \
  --query 'MetricAlarms[0].StateValue' --output text        # ALARM means it fired
aws lambda get-function-concurrency --function-name ryanruddock-guestbook
```

Reserved concurrency of `0` means it tripped. Re-arm with
`terraform apply`, or `terraform output rearm_command` for the CLI equivalent.
Work out *why* it fired before re-arming — the alarm is 1000 invocations in 5
minutes, which normal traffic will not reach.

## Loose ends

- The webring / "Prev · Random · Next" links in the footer are decorative
  `href="#"` props. Period-correct furniture, not real links.
- The guestbook is unauthenticated by design. Validation caps length, strips
  non-`http(s)` URLs, and the page renders every field with `textContent` so
  submitted markup stays inert. There is no spam filter — if it gets abused,
  that is the thing to add.
- `infra/terraform.tfstate` / `.backup` may linger locally from before the S3
  migration. They are gitignored and no longer authoritative; safe to delete.
