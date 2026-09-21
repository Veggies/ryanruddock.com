# ---------------------------------------------------------------------------
# Remote state.
#
# The bucket is deliberately NOT managed by this Terraform: a backend cannot
# create the bucket it stores its own state in. It was bootstrapped once with
# the CLI (see README) and is private, versioned, encrypted, with noncurrent
# versions expiring after 90 days.
#
# use_lockfile is S3-native locking (Terraform >= 1.10), so no DynamoDB lock
# table is needed.
# ---------------------------------------------------------------------------

terraform {
  backend "s3" {
    bucket       = "ryanruddock-com-tfstate"
    key          = "ryanruddock.com/guestbook.tfstate"
    region       = "us-east-1"
    encrypt      = true
    use_lockfile = true
  }
}
