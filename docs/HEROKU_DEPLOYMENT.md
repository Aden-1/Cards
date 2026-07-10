# Heroku Deployment

This guide is the current Heroku-specific path for deploying this app in a way that is viable right now for a real alpha launch.

It assumes:

- you are deploying the current Flask app in this repository
- you want separate `staging` and `production` apps
- you are not configuring outbound email yet
- you want a cautious single-web-dyno rollout

## Deployment Modes

Choose one of these before you start:

### Recommended Alpha Mode

- separate `staging` and `production` apps
- separate Postgres databases
- `web=1` in production
- staging web dyno only when you are actively testing, if you want to control cost

This is the safest practical option for a real alpha.

### Cheapest Viable Alpha Mode

- one `production` app
- one Heroku Postgres database
- `web=1` in production
- no always-on staging dyno

This is cheaper, but riskier because release-phase migrations and config mistakes are validated directly against production.

If you choose the cheapest mode, you can skip the staging-app creation steps below and deploy straight to production, but you should be extra disciplined about local testing first.

## Before You Start

1. Create a Heroku account and add billing.
2. Install the Heroku CLI and log in:

```powershell
heroku login
```

3. Make sure your local repo is passing the checks you plan to trust before deploy:

```powershell
.\.venv\Scripts\python.exe -m unittest -v
```

4. Confirm the repo still has these deployment files:

- [.python-version](C:/Users/adent/PycharmProjects/Cards/.python-version:1)
- [requirements.txt](C:/Users/adent/PycharmProjects/Cards/requirements.txt:1)
- [Procfile](C:/Users/adent/PycharmProjects/Cards/Procfile:1)

This app already uses a Heroku-compatible `Procfile` with:

```text
release: python -m flask db upgrade
web: gunicorn app:app ...
```

The release phase runs database migrations before the web dyno serves traffic.

## Step 1: Create The Apps

Create two separate apps:

```powershell
heroku apps:create your-cards-staging
heroku apps:create your-cards-production
```

Optional but strongly recommended: put them in one pipeline so you can promote the same tested slug from staging to production.

```powershell
heroku pipelines:create cards-pipeline -a your-cards-staging -s staging
heroku pipelines:add cards-pipeline -a your-cards-production -s production
```

References:

- [Heroku Pipelines](https://devcenter.heroku.com/articles/pipelines)

Cheap option:

- If you want the cheapest viable alpha path, create only `your-cards-production` and skip the pipeline.

## Step 2: Provision Postgres

Provision a separate database for each app.

For a small alpha, `essential-0` is the current entry-level Heroku Postgres plan for Common Runtime apps:

```powershell
heroku addons:create heroku-postgresql:essential-0 -a your-cards-staging
heroku addons:create heroku-postgresql:essential-0 -a your-cards-production
```

Important:

- Do not set `DATABASE_URL` manually on Heroku for the primary database.
- Heroku Postgres adds `DATABASE_URL` for you automatically.

References:

- [Provisioning Heroku Postgres](https://devcenter.heroku.com/articles/provisioning-heroku-postgres)
- [Choosing the Right Heroku Postgres Plan](https://devcenter.heroku.com/articles/heroku-postgres-plans)

Cheap option:

- If you skipped staging, provision Postgres only for `your-cards-production`.

## Step 3: Set Required Config Vars

Set the required non-database config on both apps.

### Staging

```powershell
heroku config:set APP_ENV=production -a your-cards-staging
heroku config:set SECRET_KEY=replace-with-a-long-random-secret -a your-cards-staging
heroku config:set TRUSTED_HOSTS=your-cards-staging.herokuapp.com -a your-cards-staging
heroku config:set PUBLIC_REGISTRATION_ENABLED=false -a your-cards-staging
heroku config:set MAX_CONTENT_LENGTH=2097152 -a your-cards-staging
heroku config:set SESSION_LIFETIME_DAYS=7 -a your-cards-staging
heroku config:set RATELIMIT_STORAGE_URI=rediss://default:password@redis.example.com:6379/0 -a your-cards-staging
heroku config:set WEB_CONCURRENCY=2 -a your-cards-staging
heroku config:set GUNICORN_TIMEOUT=25 -a your-cards-staging
heroku config:set GUNICORN_GRACEFUL_TIMEOUT=25 -a your-cards-staging
heroku config:set DB_POOL_SIZE=5 -a your-cards-staging
heroku config:set DB_MAX_OVERFLOW=2 -a your-cards-staging
heroku config:set DB_POOL_TIMEOUT=10 -a your-cards-staging
heroku config:set DB_POOL_RECYCLE=300 -a your-cards-staging
```

### Production

```powershell
heroku config:set APP_ENV=production -a your-cards-production
heroku config:set SECRET_KEY=replace-with-a-long-random-secret -a your-cards-production
heroku config:set TRUSTED_HOSTS=your-cards-production.herokuapp.com -a your-cards-production
heroku config:set PUBLIC_REGISTRATION_ENABLED=false -a your-cards-production
heroku config:set MAX_CONTENT_LENGTH=2097152 -a your-cards-production
heroku config:set SESSION_LIFETIME_DAYS=7 -a your-cards-production
heroku config:set RATELIMIT_STORAGE_URI=rediss://default:password@redis.example.com:6379/0 -a your-cards-production
heroku config:set WEB_CONCURRENCY=2 -a your-cards-production
heroku config:set GUNICORN_TIMEOUT=25 -a your-cards-production
heroku config:set GUNICORN_GRACEFUL_TIMEOUT=25 -a your-cards-production
heroku config:set DB_POOL_SIZE=5 -a your-cards-production
heroku config:set DB_MAX_OVERFLOW=2 -a your-cards-production
heroku config:set DB_POOL_TIMEOUT=10 -a your-cards-production
heroku config:set DB_POOL_RECYCLE=300 -a your-cards-production
```

Notes:

- Set `RATELIMIT_STORAGE_URI` from the Redis provider's TLS URL. Limits are shared across workers, so tune `WEB_CONCURRENCY` for capacity rather than rate-limit consistency.
- Set `TRUST_PROXY_HOPS=1`; this is what allows the application to use Heroku's client address safely instead of trusting arbitrary forwarded headers.
- If you add a custom domain later, append it to `TRUSTED_HOSTS` as a comma-separated value.
- Because you are not setting up email yet, leave `MAIL_SERVER`, `MAIL_PORT`, `MAIL_USERNAME`, `MAIL_PASSWORD`, `MAIL_DEFAULT_SENDER`, and `PASSWORD_RESET_URL_BASE` unset for now. Password reset will remain unavailable, which is acceptable for your alpha.

Reference:

- [Heroku Config Vars](https://devcenter.heroku.com/articles/config-vars)

Cheap option:

- If you skipped staging, set these config vars only on `your-cards-production`.

## Step 4: Add Git Remotes

Attach each Heroku app to your local git repo:

```powershell
heroku git:remote -a your-cards-staging -r heroku-staging
heroku git:remote -a your-cards-production -r heroku-production
```

Cheap option:

```powershell
heroku git:remote -a your-cards-production -r heroku-production
```

## Step 5: Deploy To Staging First

Deploy your current branch to staging:

```powershell
git push heroku-staging HEAD:main
```

If your Heroku app still expects `master`, use:

```powershell
git push heroku-staging HEAD:master
```

Then scale the web dyno:

```powershell
heroku ps:scale web=1 -a your-cards-staging
```

What should happen:

1. Heroku detects the Python app from `requirements.txt`.
2. The Python buildpack installs dependencies.
3. The release phase runs `python -m flask db upgrade`.
4. Gunicorn starts the Flask app using the `web` command from the `Procfile`.

References:

- [Getting Started on Heroku with Python](https://devcenter.heroku.com/articles/python)
- [The Procfile](https://devcenter.heroku.com/articles/procfile)
- [Release Phase](https://devcenter.heroku.com/articles/release-phase)

Cheap option:

- If you skipped staging, deploy this same way to `heroku-production` first:

```powershell
git push heroku-production HEAD:main
heroku ps:scale web=1 -a your-cards-production
```

- Then continue with the verification and admin-provisioning steps, but perform them against production.

## Step 6: Verify The Staging Release

Check release status and logs:

```powershell
heroku releases -a your-cards-staging
heroku logs --tail -a your-cards-staging
```

Open these endpoints:

- `https://your-cards-staging.herokuapp.com/healthz`
- `https://your-cards-staging.herokuapp.com/readyz`

Both should return success.

Cheap option:

- If you skipped staging, run these checks against `https://your-cards-production.herokuapp.com`.

## Step 7: Create The First Admin

While registration is still disabled, provision your admin account:

```powershell
heroku run --app your-cards-staging -- python -m flask provision-admin --username youradmin --email you@example.com
```

You will be prompted for the password by the CLI command.

Reference:

- [Working with One-Off Dynos](https://devcenter.heroku.com/articles/working-with-one-off-dynos)

Cheap option:

- If you skipped staging, provision the admin only on `your-cards-production`.

## Step 8: Smoke Test Staging

Before production, verify these flows in staging:

1. `GET /healthz` returns `200`.
2. `GET /readyz` returns `200`.
3. Admin login works.
4. Standard user registration works if you temporarily enable it.
5. Deck creation works.
6. Card creation and editing work.
7. Public sharing works.
8. Search works.
9. Quiz flows work.
10. Account update and account deletion work.

Because email is not configured yet, also verify this expected alpha behavior:

1. Registration works without an email.
2. The forgot-password page renders.
3. Password reset is unavailable until mail settings are configured.

Cheap option:

- If you skipped staging, perform this checklist immediately after the first production deploy, before opening registration.

## Step 9: Deploy To Production

If you are using a pipeline, promote the tested staging slug:

```powershell
heroku pipelines:promote -a your-cards-staging
```

If you are not using a pipeline, deploy directly:

```powershell
git push heroku-production HEAD:main
```

Then scale production:

```powershell
heroku ps:scale web=1 -a your-cards-production
```

Provision the production admin:

```powershell
heroku run --app your-cards-production -- python -m flask provision-admin --username youradmin --email you@example.com
```

Verify production:

```powershell
heroku releases -a your-cards-production
heroku logs --tail -a your-cards-production
```

Cheap option:

- If you already deployed directly to production earlier, this step is just your production verification pass.

## Step 10: Turn On Public Registration For The Alpha

Only after staging and production smoke tests pass:

```powershell
heroku config:set PUBLIC_REGISTRATION_ENABLED=true -a your-cards-production
```

That config change creates a new Heroku release. Since this app has a `release` process type, Heroku runs the migration release phase again before the updated release becomes active.

## Step 11: Add A Custom Domain And HTTPS Later

If you add a custom domain:

1. Add the domain in Heroku.
2. Point your DNS at the Heroku target.
3. Enable Automated Certificate Management.
4. Add the custom hostname to `TRUSTED_HOSTS`.

Example:

```powershell
heroku domains:add cards.example.com -a your-cards-production
heroku certs:auto:enable -a your-cards-production
heroku config:set TRUSTED_HOSTS=your-cards-production.herokuapp.com,cards.example.com -a your-cards-production
```

References:

- [Automated Certificate Management](https://devcenter.heroku.com/articles/automated-certificate-management)

## Step 12: Backups And Ongoing Operations

For a real deployment, do not stop at "it booted once".

At minimum:

1. Capture a database backup after launch:

```powershell
heroku pg:backups:capture -a your-cards-production
```

2. Confirm you can list backups:

```powershell
heroku pg:backups -a your-cards-production
```

3. Keep watching logs during the first user sessions:

```powershell
heroku logs --tail -a your-cards-production
```

4. Confirm `RATELIMIT_STORAGE_URI` is connected and rate-limit responses include `Retry-After` before increasing workers.

## Current Alpha Constraints

This deployment is viable right now for a real alpha, with these known constraints:

- Redis-backed shared rate limiting is required
- no email delivery yet
- password reset unavailable until SMTP config is added

Those constraints are acceptable for a small managed alpha, but they should be revisited before a broader public launch.
